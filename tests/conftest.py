from __future__ import annotations

import json
from pathlib import Path

import pytest

from cn_property_agent.domain import Community, ListingObservation, Transaction
from cn_property_agent.providers import RawTransactionRecord
from cn_property_agent.providers.lianjia import LianjiaListingParseContext, LianjiaParseContext


def _load_fixture(name: str) -> dict:
    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sample_records() -> dict:
    return _load_fixture("sample_records.json")


@pytest.fixture
def communities(sample_records: dict) -> list[Community]:
    return [Community.model_validate(item) for item in sample_records["communities"]]


@pytest.fixture
def transaction(sample_records: dict) -> Transaction:
    return Transaction.model_validate(sample_records["transaction"])


@pytest.fixture
def provider_records() -> dict[str, RawTransactionRecord]:
    fixture = _load_fixture("provider_transactions.json")
    return {
        name: RawTransactionRecord.model_validate(record)
        for name, record in fixture["records"].items()
    }


@pytest.fixture
def ingestion_community(communities: list[Community]) -> Community:
    fixture = _load_fixture("provider_transactions.json")
    return next(item for item in communities if item.community_id == fixture["community_id"])


@pytest.fixture
def provider_observations() -> dict[str, ListingObservation]:
    fixture = _load_fixture("provider_listings.json")
    return {
        name: ListingObservation.model_validate(observation)
        for name, observation in fixture["observations"].items()
    }


@pytest.fixture
def listing_community(communities: list[Community]) -> Community:
    fixture = _load_fixture("provider_listings.json")
    return next(item for item in communities if item.community_id == fixture["community_id"])


@pytest.fixture
def lianjia_fixture() -> dict:
    return _load_fixture("lianjia_transactions.json")


@pytest.fixture
def lianjia_rows(lianjia_fixture: dict) -> dict[str, dict]:
    return lianjia_fixture["rows"]


@pytest.fixture
def lianjia_context(lianjia_fixture: dict) -> LianjiaParseContext:
    return LianjiaParseContext.model_validate(lianjia_fixture["context"])


@pytest.fixture
def lianjia_listing_fixture() -> dict:
    return _load_fixture("lianjia_listings.json")


@pytest.fixture
def lianjia_listing_rows(lianjia_listing_fixture: dict) -> dict[str, dict]:
    return lianjia_listing_fixture["rows"]


@pytest.fixture
def lianjia_listing_context(lianjia_listing_fixture: dict) -> LianjiaListingParseContext:
    return LianjiaListingParseContext.model_validate(lianjia_listing_fixture["context"])


@pytest.fixture
def lianjia_later_listing_context(lianjia_listing_fixture: dict) -> LianjiaListingParseContext:
    """The same community observed at a later snapshot time."""
    return LianjiaListingParseContext.model_validate(lianjia_listing_fixture["later_context"])


@pytest.fixture
def lianjia_community(communities: list[Community], lianjia_fixture: dict) -> Community:
    community_id = lianjia_fixture["community_id"]
    return next(item for item in communities if item.community_id == community_id)
