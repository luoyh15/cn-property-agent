from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from cn_property_agent.domain import Community, Listing, ListingObservation, ListingStatus
from cn_property_agent.providers import (
    ListingFetchResult,
    ListingParseResult,
    ListingProvider,
    ParseRejection,
    ParseRejectionReason,
    SourceRowRef,
)
from cn_property_agent.services import (
    ListingIngestionService,
    ProviderContractError,
    ProviderFetchError,
    listing_ingestion,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, ListingRepository
from fakes import FakeListingProvider

LISTING_COLUMNS = (
    "listing_id",
    "community_id",
    "source",
    "source_listing_id",
    "area_sqm",
    "layout",
    "floor_bucket",
    "orientation",
    "built_year",
    "building_type",
    "first_seen_at",
    "last_seen_at",
    "status",
)


@pytest.fixture
def database(listing_community: Community) -> Iterator[DuckDBDatabase]:
    with DuckDBDatabase() as db:
        CommunityRepository(db.connection).upsert(listing_community)
        yield db


def build_service(
    database: DuckDBDatabase,
    provider: ListingProvider,
) -> tuple[ListingIngestionService, ListingRepository]:
    repository = ListingRepository(database.connection)
    return ListingIngestionService(provider=provider, repository=repository), repository


def make_provider(
    community: Community,
    observations: Sequence[ListingObservation] | ListingFetchResult,
    *,
    error: Exception | None = None,
) -> FakeListingProvider:
    return FakeListingProvider({community.community_id: observations}, error=error)


def make_parse_rejection(row_index: int, source_row_id: str) -> ParseRejection:
    return ParseRejection(
        row=SourceRowRef(source="fixture", row_index=row_index, source_row_id=source_row_id),
        reason=ParseRejectionReason.MALFORMED_FIELD,
        field="总价",
        detail="总价 '面议' is not a number",
    )


def stored_listings(database: DuckDBDatabase) -> list[Listing]:
    """Read the canonical identity rows; the listing read path is a later task."""
    rows = database.connection.execute(
        f"SELECT {', '.join(LISTING_COLUMNS)} FROM listing ORDER BY listing_id"
    ).fetchall()
    return [Listing.model_validate(dict(zip(LISTING_COLUMNS, row, strict=True))) for row in rows]


def stored_snapshot_count(database: DuckDBDatabase) -> int:
    return database.connection.execute("SELECT count(*) FROM listing_snapshot").fetchone()[0]


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeListingProvider(), ListingProvider)


@pytest.mark.asyncio
async def test_snapshot_persists_listing_identity_and_history(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    observations = [provider_observations["valid_a"], provider_observations["valid_b"]]
    provider = make_provider(listing_community, observations)
    service, repository = build_service(database, provider)

    result = await service.ingest(listing_community)

    assert (result.source_row_count, result.parsed_count) == (2, 2)
    assert result.persisted_observation_count == 2
    assert result.parse_rejections == ()
    assert result.community_id == listing_community.community_id
    assert provider.calls == [listing_community.community_id]

    assert result.listing_ids == tuple(item.listing.listing_id for item in observations)
    assert result.listing_count == 2
    assert stored_listings(database) == [item.listing for item in observations]
    for observation in observations:
        assert repository.history(observation.listing.listing_id) == [observation.snapshot]


@pytest.mark.asyncio
async def test_parse_rejection_does_not_discard_its_valid_sibling(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    valid = provider_observations["valid_a"]
    rejection = make_parse_rejection(1, "listed-999")
    fetched = ListingFetchResult.from_parse_result(
        ListingParseResult(observations=(valid,), rejections=(rejection,))
    )
    provider = make_provider(listing_community, fetched)
    service, repository = build_service(database, provider)

    result = await service.ingest(listing_community)

    assert (result.source_row_count, result.parsed_count) == (2, 1)
    assert result.persisted_observation_count == 1
    # The rejection survives ingestion in the parser's own vocabulary.
    assert result.parse_rejections == (rejection,)
    assert result.parse_rejection_count == 1
    assert result.parse_rejections[0].reason is ParseRejectionReason.MALFORMED_FIELD

    assert [item.listing_id for item in stored_listings(database)] == [valid.listing.listing_id]
    assert repository.history(valid.listing.listing_id) == [valid.snapshot]


@pytest.mark.asyncio
async def test_exact_replay_is_idempotent(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    """Re-ingesting an unchanged snapshot rewrites rows instead of adding them."""
    valid_a = provider_observations["valid_a"]
    observations = [valid_a, provider_observations["valid_b"]]
    provider = make_provider(listing_community, observations)
    service, repository = build_service(database, provider)

    first = await service.ingest(listing_community)
    after_first = stored_listings(database)
    second = await service.ingest(listing_community)
    after_second = stored_listings(database)

    assert second == first
    assert after_second == after_first
    assert len(after_second) == 2
    assert repository.history(valid_a.listing.listing_id) == [valid_a.snapshot]


@pytest.mark.asyncio
async def test_later_snapshot_appends_history_and_extends_seen_range(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    first_seen = provider_observations["valid_a"]
    later = provider_observations["valid_a_later"]
    provider = make_provider(listing_community, [first_seen])
    service, repository = build_service(database, provider)

    await service.ingest(listing_community)
    provider.observe(listing_community, [later])
    result = await service.ingest(listing_community)

    assert result.listing_ids == (first_seen.listing.listing_id,)

    identities = stored_listings(database)
    assert len(identities) == 1
    identity = identities[0]
    assert identity.first_seen_at == first_seen.listing.first_seen_at
    assert identity.last_seen_at == later.listing.last_seen_at
    assert identity.status is ListingStatus.WITHDRAWN

    history = repository.history(identity.listing_id)
    assert history == [first_seen.snapshot, later.snapshot]
    assert [item.list_price_cny for item in history] == [12000000, 11300000]


@pytest.mark.asyncio
async def test_provenance_round_trips_unchanged(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    observations = [provider_observations["valid_a"], provider_observations["sparse_provenance"]]
    provider = make_provider(listing_community, observations)
    service, repository = build_service(database, provider)

    await service.ingest(listing_community)

    for observation in observations:
        snapshot = observation.snapshot
        stored = repository.history(snapshot.listing_id)[0]
        assert stored.source == snapshot.source
        assert stored.source_url == snapshot.source_url
        assert stored.raw_payload_ref == snapshot.raw_payload_ref
        assert stored.parser_version == snapshot.parser_version
        assert stored.snapshot_at == snapshot.snapshot_at
        assert stored == snapshot

    # An absent source_url/raw_payload_ref stays absent rather than being invented.
    sparse = repository.history("lst-fixture-0003")[0]
    assert (sparse.source_url, sparse.raw_payload_ref, sparse.unit_price_cny_sqm) == (
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_empty_successful_fetch_is_not_a_failure(
    database: DuckDBDatabase,
    listing_community: Community,
) -> None:
    """A community with nothing listed is a valid observation, not a silent error."""
    service, _ = build_service(database, FakeListingProvider())

    result = await service.ingest(listing_community)

    assert (result.source_row_count, result.parsed_count) == (0, 0)
    assert (result.persisted_observation_count, result.listing_count) == (0, 0)
    assert result.parse_rejections == ()
    assert stored_listings(database) == []


@pytest.mark.asyncio
async def test_provider_failure_is_explicit_and_writes_nothing(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    failure = RuntimeError("source unavailable")
    provider = make_provider(
        listing_community,
        [provider_observations["valid_a"]],
        error=failure,
    )
    service, repository = build_service(database, provider)

    with pytest.raises(ProviderFetchError) as excinfo:
        await service.ingest(listing_community)

    assert excinfo.value.__cause__ is failure
    assert excinfo.value.subject_id == listing_community.community_id
    assert stored_listings(database) == []
    assert repository.history("lst-fixture-0001") == []


@pytest.mark.asyncio
async def test_observation_for_another_community_fails_before_any_write(
    database: DuckDBDatabase,
    listing_community: Community,
    provider_observations: dict[str, ListingObservation],
) -> None:
    """A batch that answers about the wrong community is refused whole.

    The matching observation is listed first on purpose: validating row by row
    while persisting would already have written it before reaching the stray
    one, leaving a snapshot that is half about one community and half about
    another.
    """
    matching = provider_observations["valid_a"]
    foreign = provider_observations["foreign_community"]
    assert foreign.listing.community_id != listing_community.community_id

    provider = make_provider(listing_community, [matching, foreign])
    service, repository = build_service(database, provider)

    with pytest.raises(ProviderContractError) as excinfo:
        await service.ingest(listing_community)

    assert excinfo.value.subject_id == listing_community.community_id
    assert foreign.listing.listing_id in str(excinfo.value)
    assert foreign.listing.community_id in str(excinfo.value)

    assert stored_listings(database) == []
    assert stored_snapshot_count(database) == 0
    assert repository.history(matching.listing.listing_id) == []
    assert repository.history(foreign.listing.listing_id) == []


@pytest.mark.asyncio
async def test_foreign_observation_cannot_hijack_an_existing_listing(
    database: DuckDBDatabase,
    listing_community: Community,
    communities: list[Community],
    provider_observations: dict[str, ListingObservation],
) -> None:
    """Storage keys listings by `listing_id` alone, so the guard is what protects them."""
    foreign = provider_observations["foreign_community"]
    other_community = next(
        item for item in communities if item.community_id == foreign.listing.community_id
    )
    repository = ListingRepository(database.connection)
    repository.upsert_listing(foreign.listing)
    repository.append_snapshot(foreign.snapshot)

    provider = make_provider(listing_community, [foreign])
    service, _ = build_service(database, provider)

    with pytest.raises(ProviderContractError):
        await service.ingest(listing_community)

    stored = stored_listings(database)
    assert [item.community_id for item in stored] == [other_community.community_id]
    assert repository.history(foreign.listing.listing_id) == [foreign.snapshot]


def test_service_is_source_independent() -> None:
    """The service must not reach below the provider/domain/storage boundary."""
    source = Path(listing_ingestion.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    internal = {name for name in imported if name.startswith("cn_property_agent")}
    assert internal == {
        "cn_property_agent.domain",
        "cn_property_agent.providers",
        "cn_property_agent.storage.repositories",
    }
    forbidden = (
        "lianjia",
        "beike",
        "cn_property_agent.config",
        "cn_property_agent.providers.lianjia",
        "httpx",
        "requests",
        "playwright",
    )
    assert not any(token in source.lower() for token in forbidden)
