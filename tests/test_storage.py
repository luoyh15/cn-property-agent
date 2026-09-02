from __future__ import annotations

from cn_property_agent.domain import EntityAlias, Listing, ListingSnapshot
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, ListingRepository, TransactionRepository


def test_community_and_alias_roundtrip(communities) -> None:
    with DuckDBDatabase() as database:
        repository = CommunityRepository(database.connection)
        community = communities[0]
        repository.upsert(community)
        repository.upsert_alias(
            EntityAlias(
                entity_type="community",
                entity_id=community.community_id,
                provider="fixture",
                provider_entity_id="native-001",
            )
        )

        loaded = repository.get(community.community_id)
        by_alias = repository.find_by_alias(
            entity_type="community",
            provider="fixture",
            provider_entity_id="native-001",
        )

        assert loaded == community
        assert by_alias == community


def test_transaction_roundtrip(communities, transaction) -> None:
    with DuckDBDatabase() as database:
        communities_repo = CommunityRepository(database.connection)
        communities_repo.upsert(communities[2])
        repository = TransactionRepository(database.connection)
        repository.upsert(transaction)

        rows = repository.list_for_community(transaction.community_id)

        assert len(rows) == 1
        assert rows[0] == transaction


def test_listing_snapshot_history_is_append_only() -> None:
    listing = Listing.model_validate(
        {
            "listing_id": "listing-1",
            "community_id": "community-1",
            "source": "fixture",
            "source_listing_id": "native-1",
            "area_sqm": 90,
            "first_seen_at": "2026-08-01T00:00:00Z",
            "last_seen_at": "2026-08-10T00:00:00Z",
            "status": "active",
        }
    )
    snapshots = [
        ListingSnapshot.model_validate(
            {
                "listing_id": "listing-1",
                "snapshot_at": "2026-08-01T00:00:00Z",
                "list_price_cny": 9000000,
                "status": "active",
                "source": "fixture",
                "parser_version": "v1",
            }
        ),
        ListingSnapshot.model_validate(
            {
                "listing_id": "listing-1",
                "snapshot_at": "2026-08-10T00:00:00Z",
                "list_price_cny": 8600000,
                "status": "active",
                "source": "fixture",
                "parser_version": "v1",
            }
        ),
    ]

    with DuckDBDatabase() as database:
        repository = ListingRepository(database.connection)
        repository.upsert_listing(listing)
        for snapshot in snapshots:
            repository.append_snapshot(snapshot)

        history = repository.history(listing.listing_id)

        assert [row.list_price_cny for row in history] == [9000000, 8600000]


def test_community_history_covers_every_listing_of_that_community(
    communities, provider_observations
) -> None:
    """One read returns the whole community's history and nobody else's."""
    with DuckDBDatabase() as database:
        CommunityRepository(database.connection).upsert(communities[2])
        repository = ListingRepository(database.connection)
        for key in ("valid_a", "valid_a_later", "valid_b", "foreign_community"):
            observation = provider_observations[key]
            repository.upsert_listing(observation.listing)
            repository.append_snapshot(observation.snapshot)

        history = repository.history_for_community("cm-sh-pd-002")

        assert [(row.listing_id, row.snapshot_at.date().isoformat()) for row in history] == [
            ("lst-fixture-0001", "2026-08-01"),
            ("lst-fixture-0001", "2026-09-01"),
            ("lst-fixture-0002", "2026-08-01"),
        ]
        assert repository.history_for_community("cm-sh-mh-001") == [
            provider_observations["foreign_community"].snapshot
        ]
        assert repository.history_for_community("cm-does-not-exist") == []
