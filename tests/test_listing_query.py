from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from cn_property_agent.domain import (
    Community,
    Listing,
    ListingObservation,
    ListingSnapshot,
    ListingStatus,
)
from cn_property_agent.services import CurrentListing, ListingQueryService, listing_query
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, ListingRepository

TARGET_COMMUNITY = "cm-sh-pd-002"
OTHER_COMMUNITY = "cm-sh-mh-001"

# The seeded fixtures all share this last_seen_at, so ordering falls back to id.
SEEDED_LAST_SEEN = datetime(2026, 8, 1, tzinfo=timezone.utc)
LATER_LAST_SEEN = datetime(2026, 9, 15, tzinfo=timezone.utc)


def store(repository: ListingRepository, observation: ListingObservation) -> None:
    repository.upsert_listing(observation.listing)
    repository.append_snapshot(observation.snapshot)


@pytest.fixture
def seeded(
    communities: list[Community],
    provider_observations: dict[str, ListingObservation],
) -> Iterator[tuple[ListingQueryService, ListingRepository]]:
    """Three listings in the target community plus one in another community."""
    with DuckDBDatabase() as database:
        community_repository = CommunityRepository(database.connection)
        for community in communities:
            community_repository.upsert(community)
        repository = ListingRepository(database.connection)
        for key in ("valid_a", "valid_b", "sparse_provenance", "foreign_community"):
            store(repository, provider_observations[key])
        yield ListingQueryService(repository=repository), repository


def test_current_listings_pair_each_listing_with_its_latest_snapshot(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    service, _ = seeded

    result = service.get_current_listings(TARGET_COMMUNITY)

    expected = [provider_observations[key] for key in ("valid_a", "valid_b", "sparse_provenance")]
    assert [item.listing_id for item in result] == [item.listing.listing_id for item in expected]
    assert [item.listing for item in result] == [item.listing for item in expected]
    assert [item.latest_snapshot for item in result] == [item.snapshot for item in expected]


def test_later_snapshot_replaces_the_current_view_but_history_survives(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    service, repository = seeded
    first_seen = provider_observations["valid_a"]
    later = provider_observations["valid_a_later"]
    listing_id = first_seen.listing.listing_id

    store(repository, later)
    current = {item.listing_id: item for item in service.get_current_listings(TARGET_COMMUNITY)}

    assert len(current) == 3, "a second snapshot must not duplicate the listing"
    updated = current[listing_id]
    assert updated.latest_snapshot == later.snapshot
    assert updated.latest_snapshot.list_price_cny == 11300000
    assert updated.listing.status is ListingStatus.WITHDRAWN
    # The untouched sibling keeps the snapshot it already had.
    assert current["lst-fixture-0002"].latest_snapshot == provider_observations["valid_b"].snapshot

    history = service.get_listing_history(listing_id)
    assert history == (first_seen.snapshot, later.snapshot)
    assert [item.snapshot_at for item in history] == sorted(item.snapshot_at for item in history)


def test_other_communities_are_excluded(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    service, _ = seeded
    foreign = provider_observations["foreign_community"]

    target = service.get_current_listings(TARGET_COMMUNITY)
    other = service.get_current_listings(OTHER_COMMUNITY)

    assert foreign.listing.listing_id not in {item.listing_id for item in target}
    assert all(item.listing.community_id == TARGET_COMMUNITY for item in target)
    # The excluded listing is stored and readable, just under its own community.
    assert [item.listing for item in other] == [foreign.listing]
    assert other[0].latest_snapshot == foreign.snapshot


def test_ordering_is_most_recently_seen_first_with_stable_tie_break(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    service, repository = seeded
    valid_b = provider_observations["valid_b"].listing

    first = service.get_current_listings(TARGET_COMMUNITY)
    second = service.get_current_listings(TARGET_COMMUNITY)

    # All three share last_seen_at, so listing_id decides the order.
    assert [item.listing.last_seen_at for item in first] == [SEEDED_LAST_SEEN] * 3
    assert [item.listing_id for item in first] == [
        "lst-fixture-0001",
        "lst-fixture-0002",
        "lst-fixture-0003",
    ]
    assert second == first

    repository.upsert_listing(valid_b.model_copy(update={"last_seen_at": LATER_LAST_SEEN}))
    reordered = service.get_current_listings(TARGET_COMMUNITY)

    assert [item.listing_id for item in reordered] == [
        "lst-fixture-0002",
        "lst-fixture-0001",
        "lst-fixture-0003",
    ]


def test_provenance_and_identity_round_trip_unchanged(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    service, _ = seeded
    result = {item.listing_id: item for item in service.get_current_listings(TARGET_COMMUNITY)}

    for key in ("valid_a", "valid_b", "sparse_provenance"):
        observation = provider_observations[key]
        stored = result[observation.listing.listing_id]
        snapshot = stored.latest_snapshot
        assert snapshot is not None
        assert snapshot.source == observation.snapshot.source
        assert snapshot.source_url == observation.snapshot.source_url
        assert snapshot.raw_payload_ref == observation.snapshot.raw_payload_ref
        assert snapshot.parser_version == observation.snapshot.parser_version
        assert snapshot.snapshot_at == observation.snapshot.snapshot_at
        assert snapshot == observation.snapshot
        assert stored.listing == observation.listing

    # Missing provenance stays missing rather than being filled in.
    sparse = result["lst-fixture-0003"].latest_snapshot
    assert (sparse.source_url, sparse.raw_payload_ref, sparse.unit_price_cny_sqm) == (
        None,
        None,
        None,
    )


def test_identity_without_a_stored_snapshot_reports_none(
    seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    """A listing is never given a snapshot it does not have."""
    service, repository = seeded
    identity_only = provider_observations["valid_a"].listing.model_copy(
        update={"listing_id": "lst-fixture-0009", "source_listing_id": "listed-009"}
    )
    repository.upsert_listing(identity_only)

    result = {item.listing_id: item for item in service.get_current_listings(TARGET_COMMUNITY)}

    assert result["lst-fixture-0009"].latest_snapshot is None
    assert result["lst-fixture-0009"].listing == identity_only
    assert service.get_listing_history("lst-fixture-0009") == ()
    assert result["lst-fixture-0001"].latest_snapshot is not None


def test_empty_and_unknown_reads_are_successes(seeded) -> None:
    service, _ = seeded

    assert service.get_current_listings("cm-does-not-exist") == ()
    assert service.get_current_listings("") == ()
    assert service.get_listing_history("lst-does-not-exist") == ()


def test_current_listing_rejects_a_mismatched_snapshot(
    provider_observations: dict[str, ListingObservation],
) -> None:
    listing: Listing = provider_observations["valid_a"].listing
    snapshot: ListingSnapshot = provider_observations["valid_b"].snapshot

    with pytest.raises(ValueError, match="listing_id must match"):
        CurrentListing(listing=listing, latest_snapshot=snapshot)


def test_service_is_source_independent() -> None:
    """The query service must not reach below the storage/domain boundary."""
    source = Path(listing_query.__file__).read_text(encoding="utf-8")
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
        "cn_property_agent.storage.repositories",
    }
    forbidden = (
        "lianjia",
        "beike",
        "cn_property_agent.config",
        "cn_property_agent.providers",
        "httpx",
        "requests",
        "playwright",
    )
    assert not any(token in source.lower() for token in forbidden)
