from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from cn_property_agent.analytics import (
    MINIMUM_SAMPLE_COUNT,
    compute_community_transaction_metrics,
)
from cn_property_agent.analytics import transaction_metrics
from cn_property_agent.domain import Community, Transaction
from cn_property_agent.services import TransactionQuery, TransactionQueryService
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository, TransactionRepository

COMMUNITY = "cm-sh-pd-002"
OTHER_COMMUNITY = "cm-sh-pd-001"
AREA_SQM = 100.0

# Five deals over one community. Areas are 100 sqm, so unit price is deal price
# divided by 100 and every median below can be checked by hand.
#   unit prices   90_000  95_000  100_000  105_000  110_000  -> median 100_000
#   deal prices     9.0M    9.5M     10.0M    10.5M    11.0M -> median 10.0M
#   days on market    30      50        10     None     None -> median 30 of 3
#   discounts       0.10    0.05      0.00     None     None -> median 0.05 of 3
SAMPLE = (
    ("tx-1", date(2026, 3, 1), 9_000_000.0, 30, 10_000_000.0),
    ("tx-2", date(2026, 4, 12), 9_500_000.0, 50, 10_000_000.0),
    ("tx-3", date(2026, 5, 20), 10_000_000.0, 10, 10_000_000.0),
    ("tx-4", date(2026, 6, 2), 10_500_000.0, None, None),
    ("tx-5", date(2026, 7, 15), 11_000_000.0, None, None),
)
LATEST_DEAL_DATE = date(2026, 7, 15)


def make_transaction(
    base: Transaction,
    *,
    transaction_id: str,
    deal_date: date,
    deal_price_cny: float,
    days_on_market: int | None = None,
    initial_listing_price_cny: float | None = None,
    community_id: str = COMMUNITY,
) -> Transaction:
    """Vary only the fields the metrics read; provenance stays as fixtured."""
    return base.model_copy(
        update={
            "transaction_id": transaction_id,
            "community_id": community_id,
            "deal_date": deal_date,
            "area_sqm": AREA_SQM,
            "deal_price_cny": deal_price_cny,
            "unit_price_cny_sqm": deal_price_cny / AREA_SQM,
            "days_on_market": days_on_market,
            "initial_listing_price_cny": initial_listing_price_cny,
        }
    )


@pytest.fixture
def sample(transaction: Transaction) -> tuple[Transaction, ...]:
    return tuple(
        make_transaction(
            transaction,
            transaction_id=transaction_id,
            deal_date=deal_date,
            deal_price_cny=deal_price,
            days_on_market=days,
            initial_listing_price_cny=listing_price,
        )
        for transaction_id, deal_date, deal_price, days, listing_price in SAMPLE
    )


def test_medians_match_hand_computed_values(sample: tuple[Transaction, ...]) -> None:
    metrics = compute_community_transaction_metrics(sample, community_id=COMMUNITY)

    assert metrics.community_id == COMMUNITY
    assert metrics.minimum_sample_count == MINIMUM_SAMPLE_COUNT
    assert metrics.sample_count == 5
    assert metrics.has_sufficient_evidence
    assert metrics.latest_deal_date == LATEST_DEAL_DATE
    assert metrics.median_unit_price_cny_sqm.value == pytest.approx(100_000.0)
    assert metrics.median_unit_price_cny_sqm.usable_count == 5
    assert metrics.median_deal_price_cny.value == pytest.approx(10_000_000.0)
    assert metrics.median_deal_price_cny.usable_count == 5


def test_odd_and_even_sample_sizes_use_midpoint_for_even(
    sample: tuple[Transaction, ...],
) -> None:
    odd = compute_community_transaction_metrics(sample[:3], community_id=COMMUNITY)
    even = compute_community_transaction_metrics(sample[:4], community_id=COMMUNITY)

    # 90_000 / 95_000 / 100_000 -> middle value; plus 105_000 -> midpoint of the two middles.
    assert odd.median_unit_price_cny_sqm.value == pytest.approx(95_000.0)
    assert even.median_unit_price_cny_sqm.value == pytest.approx(97_500.0)
    assert even.median_deal_price_cny.value == pytest.approx(9_750_000.0)


def test_optional_field_metrics_report_their_own_usable_count(
    sample: tuple[Transaction, ...],
) -> None:
    metrics = compute_community_transaction_metrics(sample, community_id=COMMUNITY)

    # Only three of five records carry days on market and an initial asking price.
    assert metrics.median_days_on_market_days.usable_count == 3
    assert metrics.median_days_on_market_days.value == pytest.approx(30.0)
    assert metrics.median_negotiation_discount.usable_count == 3
    assert metrics.median_negotiation_discount.value == pytest.approx(0.05)


def test_missing_optional_fields_are_not_imputed(transaction: Transaction) -> None:
    without_optionals = tuple(
        make_transaction(
            transaction,
            transaction_id=transaction_id,
            deal_date=deal_date,
            deal_price_cny=deal_price,
        )
        for transaction_id, deal_date, deal_price, _, _ in SAMPLE
    )

    metrics = compute_community_transaction_metrics(without_optionals, community_id=COMMUNITY)

    assert metrics.sample_count == 5
    assert metrics.median_unit_price_cny_sqm.value == pytest.approx(100_000.0)
    for metric in (metrics.median_days_on_market_days, metrics.median_negotiation_discount):
        assert metric.value is None
        assert metric.usable_count == 0
        assert not metric.has_value


def test_one_usable_optional_value_is_still_insufficient(
    sample: tuple[Transaction, ...],
) -> None:
    only_one_has_days = (*sample[3:], sample[0])

    metrics = compute_community_transaction_metrics(only_one_has_days, community_id=COMMUNITY)

    assert metrics.sample_count == 3
    assert metrics.median_days_on_market_days.usable_count == 1
    assert metrics.median_days_on_market_days.value is None


def test_no_transactions_is_explicit_absence_not_zero() -> None:
    metrics = compute_community_transaction_metrics((), community_id=COMMUNITY)

    assert metrics.community_id == COMMUNITY
    assert metrics.sample_count == 0
    assert not metrics.has_transactions
    assert not metrics.has_sufficient_evidence
    assert metrics.latest_deal_date is None
    assert metrics.transaction_ids == ()
    assert metrics.sources == ()
    for metric in (
        metrics.median_unit_price_cny_sqm,
        metrics.median_deal_price_cny,
        metrics.median_days_on_market_days,
        metrics.median_negotiation_discount,
    ):
        assert metric.value is None
        assert metric.usable_count == 0


def test_sample_below_the_minimum_reports_facts_without_medians(
    sample: tuple[Transaction, ...],
) -> None:
    metrics = compute_community_transaction_metrics(sample[:2], community_id=COMMUNITY)

    assert metrics.sample_count == 2
    assert not metrics.has_sufficient_evidence
    # The evidence itself is still reported; only the summarizing medians are withheld.
    assert metrics.latest_deal_date == date(2026, 4, 12)
    assert metrics.transaction_ids == ("tx-1", "tx-2")
    assert metrics.median_unit_price_cny_sqm.value is None
    assert metrics.median_unit_price_cny_sqm.usable_count == 2
    assert metrics.median_deal_price_cny.value is None


def test_minimum_sample_count_is_configurable(sample: tuple[Transaction, ...]) -> None:
    relaxed = compute_community_transaction_metrics(
        sample[:2], community_id=COMMUNITY, minimum_sample_count=2
    )
    strict = compute_community_transaction_metrics(
        sample, community_id=COMMUNITY, minimum_sample_count=6
    )

    assert relaxed.minimum_sample_count == 2
    assert relaxed.median_unit_price_cny_sqm.value == pytest.approx(92_500.0)
    assert strict.sample_count == 5
    assert not strict.has_sufficient_evidence
    assert strict.median_unit_price_cny_sqm.value is None
    assert strict.median_unit_price_cny_sqm.usable_count == 5


def test_repeated_calls_and_input_order_do_not_change_the_result(
    sample: tuple[Transaction, ...],
) -> None:
    first = compute_community_transaction_metrics(sample, community_id=COMMUNITY)
    second = compute_community_transaction_metrics(sample, community_id=COMMUNITY)
    reversed_order = compute_community_transaction_metrics(
        tuple(reversed(sample)), community_id=COMMUNITY
    )

    assert second == first
    assert reversed_order == first


def test_evidence_ids_and_sources_are_exposed(sample: tuple[Transaction, ...]) -> None:
    metrics = compute_community_transaction_metrics(sample, community_id=COMMUNITY)

    assert metrics.transaction_ids == ("tx-1", "tx-2", "tx-3", "tx-4", "tx-5")
    assert metrics.sources == (sample[0].source,)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"community_id": "  "}, "community_id must not be blank"),
        ({"community_id": COMMUNITY, "minimum_sample_count": 0}, "at least 1"),
        ({"community_id": COMMUNITY, "minimum_sample_count": True}, "must be an integer"),
        ({"community_id": COMMUNITY, "minimum_sample_count": 2.5}, "must be an integer"),
    ],
)
def test_invalid_arguments_are_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        compute_community_transaction_metrics((), **kwargs)


def test_records_from_another_community_are_an_error(
    sample: tuple[Transaction, ...], transaction: Transaction
) -> None:
    mixed = (
        *sample,
        make_transaction(
            transaction,
            transaction_id="tx-other",
            deal_date=date(2026, 5, 1),
            deal_price_cny=9_000_000.0,
            community_id=OTHER_COMMUNITY,
        ),
    )

    with pytest.raises(ValueError, match=OTHER_COMMUNITY):
        compute_community_transaction_metrics(mixed, community_id=COMMUNITY)


def test_analytics_is_source_independent() -> None:
    """Analytics may see canonical records only, never where they came from."""
    source = Path(transaction_metrics.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    internal = {name for name in imported if name.startswith("cn_property_agent")}
    assert internal == {"cn_property_agent.domain"}
    forbidden = (
        "lianjia",
        "beike",
        "shanghai",
        "httpx",
        "duckdb",
        "cn_property_agent.config",
        "cn_property_agent.providers",
        "cn_property_agent.services",
        "cn_property_agent.storage",
    )
    assert not any(token in source.lower() for token in forbidden)


@pytest.fixture
def service(
    communities: list[Community], sample: tuple[Transaction, ...]
) -> Iterator[TransactionQueryService]:
    with DuckDBDatabase() as database:
        community_repository = CommunityRepository(database.connection)
        for community in communities:
            community_repository.upsert(community)
        repository = TransactionRepository(database.connection)
        repository.upsert_many(sample)
        yield TransactionQueryService(repository=repository)


def test_service_composes_query_and_metrics(service: TransactionQueryService) -> None:
    query = TransactionQuery(community_id=COMMUNITY)

    metrics = service.get_transaction_metrics(query)
    records = service.get_transactions(query)

    assert metrics.sample_count == len(records) == 5
    assert metrics.latest_deal_date == LATEST_DEAL_DATE
    assert metrics.median_unit_price_cny_sqm.value == pytest.approx(100_000.0)
    assert metrics.transaction_ids == tuple(sorted(item.transaction_id for item in records))
    # The read path still hands back full records, provenance included.
    assert all(item.collected_at is not None and item.parser_version for item in records)


def test_service_metrics_respect_the_query_window(service: TransactionQueryService) -> None:
    windowed = service.get_transaction_metrics(
        TransactionQuery(community_id=COMMUNITY, start_date=date(2026, 5, 1))
    )

    assert windowed.sample_count == 3
    assert windowed.transaction_ids == ("tx-3", "tx-4", "tx-5")
    assert windowed.median_deal_price_cny.value == pytest.approx(10_500_000.0)


def test_service_metrics_for_a_community_without_transactions(
    service: TransactionQueryService,
) -> None:
    metrics = service.get_transaction_metrics(TransactionQuery(community_id=OTHER_COMMUNITY))

    assert metrics.community_id == OTHER_COMMUNITY
    assert metrics.sample_count == 0
    assert not metrics.has_transactions
    assert metrics.median_unit_price_cny_sqm.value is None
