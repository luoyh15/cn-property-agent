from __future__ import annotations

import pytest
from pydantic import ValidationError

from cn_property_agent.domain import Community, Listing, ListingObservation, ListingSnapshot


def test_community_rejects_partial_coordinates() -> None:
    with pytest.raises(ValidationError):
        Community(
            community_id="x",
            city_code="shanghai",
            canonical_name="测试小区",
            latitude=31.2,
        )


def test_community_rejects_inverted_build_year_range() -> None:
    with pytest.raises(ValidationError):
        Community(
            community_id="x",
            city_code="shanghai",
            canonical_name="测试小区",
            built_year_min=2010,
            built_year_max=2000,
        )


def test_transaction_fixture_has_positive_prices(transaction) -> None:
    assert transaction.deal_price_cny > 0
    assert transaction.unit_price_cny_sqm > 0


def test_listing_observation_requires_matching_identity() -> None:
    listing = Listing.model_validate(
        {
            "listing_id": "l1",
            "community_id": "c1",
            "source": "fixture",
            "source_listing_id": "source-l1",
            "first_seen_at": "2026-08-01T00:00:00Z",
            "last_seen_at": "2026-08-02T00:00:00Z",
            "status": "active",
        }
    )
    snapshot = ListingSnapshot.model_validate(
        {
            "listing_id": "l2",
            "snapshot_at": "2026-08-02T00:00:00Z",
            "list_price_cny": 1000000,
            "status": "active",
            "source": "fixture",
            "parser_version": "v1",
        }
    )
    with pytest.raises(ValidationError):
        ListingObservation(listing=listing, snapshot=snapshot)
