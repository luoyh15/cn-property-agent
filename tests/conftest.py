from __future__ import annotations

import json
from pathlib import Path

import pytest

from cn_property_agent.domain import Community, Transaction
from cn_property_agent.providers import RawTransactionRecord


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
