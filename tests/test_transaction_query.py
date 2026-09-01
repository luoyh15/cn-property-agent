from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import pytest

from cn_property_agent.domain import Community, Transaction
from cn_property_agent.services import TransactionQuery, TransactionQueryService, transaction_query
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, TransactionRepository

TARGET_COMMUNITY = "cm-sh-pd-002"
OTHER_COMMUNITY = "cm-sh-pd-001"

# Deal dates used by the seeded rows, oldest first.
DEAL_DATES = (date(2026, 3, 1), date(2026, 5, 20), date(2026, 7, 15))
ONE_DAY = timedelta(days=1)


def make_transaction(
    base: Transaction,
    *,
    transaction_id: str,
    deal_date: date,
    community_id: str = TARGET_COMMUNITY,
) -> Transaction:
    """Vary only identity, community and date; provenance stays as fixtured."""
    return base.model_copy(
        update={
            "transaction_id": transaction_id,
            "community_id": community_id,
            "deal_date": deal_date,
        }
    )


@pytest.fixture
def seeded(
    communities: list[Community],
    transaction: Transaction,
) -> Iterator[tuple[TransactionQueryService, TransactionRepository, dict[str, Transaction]]]:
    stored = {
        "old": make_transaction(transaction, transaction_id="tx-old", deal_date=DEAL_DATES[0]),
        # Same deal date as "newest", so ordering must fall back to transaction_id.
        "mid_b": make_transaction(transaction, transaction_id="tx-b", deal_date=DEAL_DATES[1]),
        "mid_a": make_transaction(transaction, transaction_id="tx-a", deal_date=DEAL_DATES[1]),
        "newest": make_transaction(transaction, transaction_id="tx-new", deal_date=DEAL_DATES[2]),
        "other_community": make_transaction(
            transaction,
            transaction_id="tx-other",
            deal_date=DEAL_DATES[1],
            community_id=OTHER_COMMUNITY,
        ),
    }
    with DuckDBDatabase() as database:
        community_repository = CommunityRepository(database.connection)
        for community in communities:
            community_repository.upsert(community)
        repository = TransactionRepository(database.connection)
        repository.upsert_many(stored.values())
        yield TransactionQueryService(repository=repository), repository, stored


def test_query_returns_only_the_requested_community(seeded) -> None:
    service, _, stored = seeded

    result = service.get_transactions(TransactionQuery(community_id=TARGET_COMMUNITY))

    assert {item.transaction_id for item in result} == {
        stored[key].transaction_id for key in ("old", "mid_a", "mid_b", "newest")
    }
    assert all(item.community_id == TARGET_COMMUNITY for item in result)


def test_date_range_bounds_are_inclusive(seeded) -> None:
    service, _, _ = seeded

    on_the_bounds = service.get_transactions(
        TransactionQuery(
            community_id=TARGET_COMMUNITY,
            start_date=DEAL_DATES[0],
            end_date=DEAL_DATES[2],
        )
    )
    single_day = service.get_transactions(
        TransactionQuery(
            community_id=TARGET_COMMUNITY,
            start_date=DEAL_DATES[1],
            end_date=DEAL_DATES[1],
        )
    )

    assert [item.transaction_id for item in on_the_bounds] == ["tx-new", "tx-a", "tx-b", "tx-old"]
    assert [item.transaction_id for item in single_day] == ["tx-a", "tx-b"]


def test_date_range_excludes_rows_just_outside_the_window(seeded) -> None:
    service, _, _ = seeded

    narrowed = service.get_transactions(
        TransactionQuery(
            community_id=TARGET_COMMUNITY,
            start_date=DEAL_DATES[0] + ONE_DAY,
            end_date=DEAL_DATES[2] - ONE_DAY,
        )
    )

    assert [item.transaction_id for item in narrowed] == ["tx-a", "tx-b"]


def test_open_ended_bounds_are_independent(seeded) -> None:
    service, _, _ = seeded

    from_mid = service.get_transactions(
        TransactionQuery(community_id=TARGET_COMMUNITY, start_date=DEAL_DATES[1])
    )
    until_mid = service.get_transactions(
        TransactionQuery(community_id=TARGET_COMMUNITY, end_date=DEAL_DATES[1])
    )

    assert [item.transaction_id for item in from_mid] == ["tx-new", "tx-a", "tx-b"]
    assert [item.transaction_id for item in until_mid] == ["tx-a", "tx-b", "tx-old"]


def test_ordering_is_newest_first_with_stable_tie_break(seeded) -> None:
    service, _, _ = seeded
    query = TransactionQuery(community_id=TARGET_COMMUNITY)

    first = service.get_transactions(query)
    second = service.get_transactions(query)

    assert [item.transaction_id for item in first] == ["tx-new", "tx-a", "tx-b", "tx-old"]
    assert [item.deal_date for item in first] == [
        DEAL_DATES[2],
        DEAL_DATES[1],
        DEAL_DATES[1],
        DEAL_DATES[0],
    ]
    assert second == first


def test_no_matching_rows_is_an_empty_success(seeded) -> None:
    service, _, _ = seeded

    unknown_community = service.get_transactions(TransactionQuery(community_id="cm-does-not-exist"))
    empty_window = service.get_transactions(
        TransactionQuery(
            community_id=TARGET_COMMUNITY,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
        )
    )

    assert unknown_community == ()
    assert empty_window == ()


def test_provenance_survives_the_read_path_unchanged(seeded, transaction: Transaction) -> None:
    service, _, stored = seeded

    result = service.get_transactions(
        TransactionQuery(community_id=TARGET_COMMUNITY, start_date=DEAL_DATES[2])
    )

    assert result == (stored["newest"],)
    newest = result[0]
    assert newest.source == transaction.source
    assert newest.source_transaction_id == transaction.source_transaction_id
    assert newest.source_url == transaction.source_url
    assert newest.raw_payload_ref == transaction.raw_payload_ref
    assert newest.collected_at == transaction.collected_at
    assert newest.parser_version == transaction.parser_version
    assert newest.initial_listing_price_cny == transaction.initial_listing_price_cny
    assert newest.deal_price_cny == transaction.deal_price_cny
    assert newest.unit_price_cny_sqm == transaction.unit_price_cny_sqm
    assert newest.days_on_market == transaction.days_on_market


def test_re_upserted_record_is_returned_once(seeded) -> None:
    """`transaction_id` is the storage key, so re-ingestion cannot duplicate a row."""
    service, repository, stored = seeded

    repository.upsert(stored["newest"])
    result = service.get_transactions(TransactionQuery(community_id=TARGET_COMMUNITY))

    ids = [item.transaction_id for item in result]
    assert ids == sorted(set(ids), key=ids.index)
    assert ids.count("tx-new") == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"community_id": ""}, "community_id"),
        ({"community_id": "   "}, "community_id must not be blank"),
        (
            {
                "community_id": TARGET_COMMUNITY,
                "start_date": DEAL_DATES[2],
                "end_date": DEAL_DATES[0],
            },
            "start_date must not be after end_date",
        ),
        ({"community_id": TARGET_COMMUNITY, "source": "lianjia"}, "source"),
    ],
)
def test_invalid_query_input_is_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        TransactionQuery(**kwargs)


def test_service_is_source_independent() -> None:
    """The query service must not reach below the storage/domain boundary."""
    source = Path(transaction_query.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    internal = {name for name in imported if name.startswith("cn_property_agent")}
    assert internal == {
        "cn_property_agent.analytics",
        "cn_property_agent.domain",
        "cn_property_agent.storage.repositories",
    }
    forbidden = ("lianjia", "beike", "cn_property_agent.config", "cn_property_agent.providers")
    assert not any(token in source.lower() for token in forbidden)
