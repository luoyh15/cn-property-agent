from __future__ import annotations

from datetime import date
from typing import Iterator

import pytest

from cn_property_agent.domain import Community, FloorBucket
from cn_property_agent.providers import TransactionFetchResult
from cn_property_agent.providers.lianjia import (
    LIANJIA_SOURCE,
    LIANJIA_TRANSACTION_PARSER_VERSION,
    LianjiaParseContext,
    parse_transaction_rows,
)
from cn_property_agent.services import (
    RejectionReason,
    TransactionIngestionRequest,
    TransactionIngestionService,
    build_transaction_id,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, TransactionRepository
from fakes import FakeTransactionProvider

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 8, 1)


@pytest.fixture
def database(lianjia_community: Community) -> Iterator[DuckDBDatabase]:
    with DuckDBDatabase() as db:
        CommunityRepository(db.connection).upsert(lianjia_community)
        yield db


async def ingest(
    database: DuckDBDatabase,
    community: Community,
    rows: list[dict],
    context: LianjiaParseContext,
):
    """Parse Lianjia rows, then run them through the source-independent service.

    The parse result travels as a whole through the provider contract, so parser
    rejections reach the service exactly as a real adapter would deliver them.
    """
    parsed = parse_transaction_rows(rows, context=context)
    fetched = TransactionFetchResult.from_parse_result(parsed)
    provider = FakeTransactionProvider({community.community_id: fetched})
    repository = TransactionRepository(database.connection)
    service = TransactionIngestionService(provider=provider, repository=repository)
    result = await service.ingest(
        TransactionIngestionRequest(
            community=community,
            start_date=WINDOW_START,
            end_date=WINDOW_END,
        )
    )
    return parsed, result, repository


@pytest.mark.asyncio
async def test_parsed_rows_round_trip_into_duckdb(
    database: DuckDBDatabase,
    lianjia_community: Community,
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rows = [lianjia_rows[name] for name in ("valid_full", "valid_title_fallback")]

    parsed, result, repository = await ingest(database, lianjia_community, rows, lianjia_context)

    assert (parsed.parsed_count, parsed.rejected_count) == (2, 0)
    assert (result.source_row_count, result.parsed_count, result.upserted_count) == (2, 2, 2)
    assert result.rejection_count == 0

    stored = {
        item.source_transaction_id: item
        for item in repository.list_for_community(lianjia_community.community_id)
    }
    assert set(stored) == {"SH1234567890", "SH1234567891"}

    newest = stored["SH1234567890"]
    assert newest.community_id == lianjia_community.community_id
    assert newest.transaction_id == build_transaction_id(LIANJIA_SOURCE, "SH1234567890")
    assert newest.deal_date == date(2026, 7, 15)
    assert newest.area_sqm == pytest.approx(120.5)
    assert newest.layout == "3室2厅"
    assert newest.floor_bucket is FloorBucket.MID
    assert newest.orientation == "南"
    assert newest.built_year == 2008
    assert newest.initial_listing_price_cny == pytest.approx(12_000_000.0)
    assert newest.deal_price_cny == pytest.approx(11_400_000.0)
    assert newest.unit_price_cny_sqm == pytest.approx(94_606.0)
    assert newest.days_on_market == 41


@pytest.mark.asyncio
async def test_provenance_survives_parse_and_storage(
    database: DuckDBDatabase,
    lianjia_community: Community,
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rows = [lianjia_rows["valid_full"], lianjia_rows["valid_title_fallback"]]

    _, _, repository = await ingest(database, lianjia_community, rows, lianjia_context)

    stored = {
        item.source_transaction_id: item
        for item in repository.list_for_community(lianjia_community.community_id)
    }
    row_level = stored["SH1234567890"]
    assert row_level.source == LIANJIA_SOURCE
    assert row_level.source_url == "https://example.invalid/lianjia/chengjiao/SH1234567890.html"
    assert row_level.raw_payload_ref == "fixture://lianjia/chengjiao/SH1234567890"
    assert row_level.collected_at == lianjia_context.collected_at
    assert row_level.parser_version == LIANJIA_TRANSACTION_PARSER_VERSION

    batch_level = stored["SH1234567891"]
    assert batch_level.source_url == lianjia_context.source_url
    assert batch_level.raw_payload_ref == lianjia_context.raw_payload_ref


@pytest.mark.asyncio
async def test_one_malformed_row_costs_only_that_row(
    database: DuckDBDatabase,
    lianjia_community: Community,
    lianjia_fixture: dict,
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rows = [lianjia_rows[name] for name in lianjia_fixture["batches"]["one_valid_one_malformed"]]

    parsed, result, repository = await ingest(database, lianjia_community, rows, lianjia_context)

    assert (parsed.parsed_count, parsed.rejected_count) == (1, 1)
    # Two source rows in, one transaction stored, and the malformed row is
    # visible in the ingestion result rather than silently missing.
    assert (result.source_row_count, result.parsed_count, result.upserted_count) == (2, 1, 1)
    assert (result.parse_rejection_count, result.quality_rejection_count) == (1, 0)
    assert result.parse_rejections == parsed.rejections

    rejection = result.parse_rejections[0]
    assert rejection.row.source == LIANJIA_SOURCE
    assert rejection.row.source_row_id == "SH1234567894"
    assert rejection.row.row_index == 1
    assert rejection.field == "建筑面积"

    stored = repository.list_for_community(lianjia_community.community_id)
    assert [item.source_transaction_id for item in stored] == ["SH1234567890"]


@pytest.mark.asyncio
async def test_parse_failures_stay_separate_from_quality_rejections(
    database: DuckDBDatabase,
    lianjia_community: Community,
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rows = [
        lianjia_rows["valid_full"],
        lianjia_rows["malformed_area"],
        lianjia_rows["quality_gate_negative_area"],
    ]

    parsed, result, _ = await ingest(database, lianjia_community, rows, lianjia_context)

    # Unintelligible text is a parser problem; a well-formed but implausible
    # value is a canonical data-quality problem. Both reach the caller, in
    # their own vocabulary and under their own count.
    assert [rejection.field for rejection in parsed.rejections] == ["建筑面积"]
    assert parsed.records[1].area_sqm == pytest.approx(-90.0)
    assert [rejection.field for rejection in result.parse_rejections] == ["建筑面积"]
    assert [rejection.reason for rejection in result.quality_rejections] == [
        RejectionReason.INVALID_AREA
    ]
    assert (result.source_row_count, result.parsed_count, result.upserted_count) == (3, 2, 1)
    assert result.rejection_count == 2
