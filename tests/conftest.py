from __future__ import annotations

import json
from pathlib import Path

import pytest

from cn_property_agent.domain import Community, Transaction


@pytest.fixture
def sample_records() -> dict:
    path = Path(__file__).parent / "fixtures" / "sample_records.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def communities(sample_records: dict) -> list[Community]:
    return [Community.model_validate(item) for item in sample_records["communities"]]


@pytest.fixture
def transaction(sample_records: dict) -> Transaction:
    return Transaction.model_validate(sample_records["transaction"])
