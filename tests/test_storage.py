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
