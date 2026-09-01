from __future__ import annotations

from datetime import date
from typing import Iterator, Sequence

import pytest

from cn_property_agent.domain import Community, FloorBucket
from cn_property_agent.providers import RawTransactionRecord, TransactionProvider
from cn_property_agent.services import (
    MAX_UNIT_PRICE_TOLERANCE,
    ProviderFetchError,
    RejectionReason,
    TransactionIngestionRequest,
    TransactionIngestionService,
    TransactionRejection,
    build_transaction_id,
    normalize_transaction,
    validate_unit_price_tolerance,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, TransactionRepository
from fakes import FakeTransactionProvider

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 8, 1)


@pytest.fixture
def database(ingestion_community: Community) -> Iterator[DuckDBDatabase]:
    with DuckDBDatabase() as db:
        CommunityRepository(db.connection).upsert(ingestion_community)
        yield db


def build_service(
    database: DuckDBDatabase,
    provider: TransactionProvider,
) -> tuple[TransactionIngestionService, TransactionRepository]:
    repository = TransactionRepository(database.connection)
    return TransactionIngestionService(provider=provider, repository=repository), repository


def make_provider(
    community: Community,
    records: Sequence[RawTransactionRecord],
    *,
    error: Exception | None = None,
) -> FakeTransactionProvider:
    return FakeTransactionProvider({community.community_id: records}, error=error)


def request_for(community: Community) -> TransactionIngestionRequest:
    return TransactionIngestionRequest(
        community=community,
        start_date=WINDOW_START,
        end_date=WINDOW_END,
    )


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeTransactionProvider(), TransactionProvider)


@pytest.mark.asyncio
async def test_ingestion_persists_canonical_transactions_with_provenance(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    raw = provider_records["valid_a"]
    provider = make_provider(ingestion_community, [raw, provider_records["valid_b"]])
    service, repository = build_service(database, provider)

    result = await service.ingest(request_for(ingestion_community))

    assert (result.fetched_count, result.upserted_count, result.rejected_count) == (2, 2, 0)
    assert result.rejections == ()
    assert provider.calls == [(ingestion_community.community_id, WINDOW_START, WINDOW_END)]

    stored = repository.list_for_community(ingestion_community.community_id)
    assert {item.transaction_id for item in stored} == set(result.transaction_ids)

    newest = stored[0]
    assert newest.community_id == ingestion_community.community_id
    assert newest.source == raw.source
    assert newest.source_transaction_id == raw.source_transaction_id
    assert newest.source_url == raw.source_url
    assert newest.raw_payload_ref == raw.raw_payload_ref
    assert newest.collected_at == raw.collected_at
    assert newest.parser_version == raw.parser_version
    assert newest.deal_price_cny == raw.deal_price_cny
    assert newest.initial_listing_price_cny == raw.initial_listing_price_cny
    assert newest.days_on_market == raw.days_on_market


@pytest.mark.asyncio
async def test_transaction_id_is_derived_from_source_identity(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    raw = provider_records["valid_a"]
    provider = make_provider(ingestion_community, [raw])
    service, _ = build_service(database, provider)

    result = await service.ingest(request_for(ingestion_community))

    assert result.transaction_ids == (build_transaction_id(raw.source, "sold-001"),)


@pytest.mark.asyncio
async def test_repeated_ingestion_is_idempotent(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    records = [provider_records["valid_a"], provider_records["valid_b"]]
    provider = make_provider(ingestion_community, records)
    service, repository = build_service(database, provider)

    first = await service.ingest(request_for(ingestion_community))
    after_first = repository.list_for_community(ingestion_community.community_id)
    second = await service.ingest(request_for(ingestion_community))
    after_second = repository.list_for_community(ingestion_community.community_id)

    assert first.transaction_ids == second.transaction_ids
    assert second.upserted_count == 2
    assert len(after_second) == len(after_first) == 2
    assert after_second == after_first


@pytest.mark.asyncio
async def test_invalid_records_are_rejected_without_failing_the_batch(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    expected_reasons = {
        "missing_identity": RejectionReason.MISSING_SOURCE_IDENTITY,
        "missing_deal_date": RejectionReason.MISSING_DEAL_DATE,
        "future_deal_date": RejectionReason.DEAL_DATE_IN_FUTURE,
        "before_requested_range": RejectionReason.DEAL_DATE_OUT_OF_RANGE,
        "negative_area": RejectionReason.INVALID_AREA,
        "zero_deal_price": RejectionReason.INVALID_DEAL_PRICE,
        "inconsistent_unit_price": RejectionReason.INCONSISTENT_UNIT_PRICE,
        "negative_days_on_market": RejectionReason.INVALID_DAYS_ON_MARKET,
        "implausible_built_year": RejectionReason.SCHEMA_INVALID,
    }
    invalid = [provider_records[name] for name in expected_reasons]
    provider = make_provider(ingestion_community, [provider_records["valid_a"], *invalid])
    service, repository = build_service(database, provider)

    result = await service.ingest(request_for(ingestion_community))

    assert result.fetched_count == len(invalid) + 1
    assert result.upserted_count == 1
    assert result.rejected_count == len(invalid)
    assert [rejection.reason for rejection in result.rejections] == list(expected_reasons.values())
    assert all(rejection.source == "fixture" for rejection in result.rejections)
    assert all(rejection.detail for rejection in result.rejections)

    stored = repository.list_for_community(ingestion_community.community_id)
    assert [item.source_transaction_id for item in stored] == ["sold-001"]


@pytest.mark.asyncio
async def test_duplicate_records_in_one_batch_are_rejected_once(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    records = [provider_records["valid_a"], provider_records["duplicate_of_valid_a"]]
    provider = make_provider(ingestion_community, records)
    service, repository = build_service(database, provider)

    result = await service.ingest(request_for(ingestion_community))

    assert (result.fetched_count, result.upserted_count, result.rejected_count) == (2, 1, 1)
    assert result.rejections[0].reason is RejectionReason.DUPLICATE_IN_BATCH
    assert len(repository.list_for_community(ingestion_community.community_id)) == 1


@pytest.mark.asyncio
async def test_derivations_and_coercions_are_reported_as_warnings(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    derived = provider_records["missing_unit_price"]
    unknown_floor = provider_records["unknown_floor_bucket"]
    provider = make_provider(ingestion_community, [derived, unknown_floor])
    service, repository = build_service(database, provider)

    result = await service.ingest(request_for(ingestion_community))

    assert result.upserted_count == 2
    assert len(result.warnings) == 2
    assert any("derived from deal_price_cny" in warning for warning in result.warnings)
    assert any("unrecognized floor_bucket" in warning for warning in result.warnings)

    stored = {
        item.source_transaction_id: item
        for item in repository.list_for_community(ingestion_community.community_id)
    }
    assert stored["sold-003"].unit_price_cny_sqm == pytest.approx(90000.0)
    assert stored["sold-004"].floor_bucket is FloorBucket.UNKNOWN


@pytest.mark.asyncio
async def test_provider_failure_is_explicit_and_writes_nothing(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    failure = RuntimeError("source unavailable")
    provider = make_provider(ingestion_community, [provider_records["valid_a"]], error=failure)
    service, repository = build_service(database, provider)

    with pytest.raises(ProviderFetchError) as excinfo:
        await service.ingest(request_for(ingestion_community))

    assert excinfo.value.__cause__ is failure
    assert excinfo.value.subject_id == ingestion_community.community_id
    assert repository.list_for_community(ingestion_community.community_id) == []


@pytest.mark.asyncio
async def test_open_date_range_accepts_every_dated_record(
    database: DuckDBDatabase,
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    provider = make_provider(
        ingestion_community,
        [provider_records["valid_a"], provider_records["before_requested_range"]],
    )
    service, _ = build_service(database, provider)

    result = await service.ingest(TransactionIngestionRequest(community=ingestion_community))

    assert (result.upserted_count, result.rejected_count) == (2, 0)


def test_ingestion_request_rejects_inverted_range(ingestion_community: Community) -> None:
    with pytest.raises(ValueError, match="start_date must not be after end_date"):
        TransactionIngestionRequest(
            community=ingestion_community,
            start_date=WINDOW_END,
            end_date=WINDOW_START,
        )


def test_normalize_transaction_flags_unit_price_inconsistency(
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    outcome = normalize_transaction(
        provider_records["inconsistent_unit_price"],
        community=ingestion_community,
    )

    assert isinstance(outcome, TransactionRejection)
    assert outcome.reason is RejectionReason.INCONSISTENT_UNIT_PRICE
    assert "44.44%" in outcome.detail


@pytest.mark.parametrize("tolerance", [-0.01, 1.5, float("nan"), float("inf"), True, "0.02"])
def test_invalid_unit_price_tolerance_is_rejected_at_configuration_time(
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
    tolerance: object,
) -> None:
    with pytest.raises(ValueError, match="unit_price_tolerance"):
        TransactionIngestionService(
            provider=FakeTransactionProvider(),
            repository=None,
            unit_price_tolerance=tolerance,
        )

    with pytest.raises(ValueError, match="unit_price_tolerance"):
        normalize_transaction(
            provider_records["valid_a"],
            community=ingestion_community,
            unit_price_tolerance=tolerance,
        )


def test_valid_unit_price_tolerance_bounds_are_accepted() -> None:
    assert validate_unit_price_tolerance(0) == 0.0
    assert validate_unit_price_tolerance(MAX_UNIT_PRICE_TOLERANCE) == MAX_UNIT_PRICE_TOLERANCE


def test_normalize_transaction_tolerates_small_rounding(
    ingestion_community: Community,
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    outcome = normalize_transaction(provider_records["valid_a"], community=ingestion_community)

    assert not isinstance(outcome, TransactionRejection)
    assert outcome.warnings == ()
    assert outcome.transaction.floor_bucket is FloorBucket.MID
