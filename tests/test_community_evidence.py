from __future__ import annotations

import ast
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb
import pytest

from cn_property_agent.analytics import (
    CommunityListingMetrics,
    CommunityTransactionMetrics,
    compute_community_listing_metrics,
    compute_community_transaction_metrics,
)
from cn_property_agent.domain import Community, ListingObservation, ListingStatus, Transaction
from cn_property_agent.services import (
    CommunityEvidenceService,
    CommunityMarketEvidence,
    ListingQueryService,
    TransactionQuery,
    TransactionQueryService,
    community_evidence,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import (
    CommunityRepository,
    ListingRepository,
    TransactionRepository,
)

TARGET_COMMUNITY = "cm-sh-pd-002"
OTHER_COMMUNITY = "cm-sh-mh-001"

# Deal dates of the seeded target-community transactions, oldest first.
DEAL_DATES = (date(2026, 3, 1), date(2026, 5, 20), date(2026, 7, 15))
OTHER_DEAL_DATE = date(2026, 8, 20)

FIRST_SNAPSHOT_AT = datetime(2026, 8, 1, tzinfo=timezone.utc)
LATER_SNAPSHOT_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)

# lst-fixture-0001 asked 12.0M on 2026-08-01 and 11.3M on 2026-09-01.
EXPECTED_PRICE_CHANGE_RATIO = (11_300_000 - 12_000_000) / 12_000_000

# The seeded target community: three listings (four snapshots, one of them a
# second observation of lst-fixture-0001) and three transactions.
TARGET_LISTING_KEYS = ("valid_a", "valid_b", "sparse_provenance", "valid_a_later")


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


def store(repository: ListingRepository, observation: ListingObservation) -> None:
    repository.upsert_listing(observation.listing)
    repository.append_snapshot(observation.snapshot)


class Seeded:
    """Storage holding two communities' evidence, plus the records it holds."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        transactions: dict[str, Transaction],
        observations: dict[str, ListingObservation],
    ) -> None:
        self.connection = connection
        self.transactions = transactions
        self.observations = observations
        self.transaction_repository = TransactionRepository(connection)
        self.listing_repository = ListingRepository(connection)
        self.service = build_service(self.transaction_repository, self.listing_repository)


def build_service(
    transactions: TransactionRepository, listings: ListingRepository
) -> CommunityEvidenceService:
    return CommunityEvidenceService(
        transactions=TransactionQueryService(repository=transactions),
        listings=ListingQueryService(repository=listings),
    )


@pytest.fixture
def seeded(
    communities: list[Community],
    transaction: Transaction,
    provider_observations: dict[str, ListingObservation],
) -> Iterator[Seeded]:
    stored = {
        "old": make_transaction(transaction, transaction_id="tx-old", deal_date=DEAL_DATES[0]),
        "mid": make_transaction(transaction, transaction_id="tx-mid", deal_date=DEAL_DATES[1]),
        "newest": make_transaction(transaction, transaction_id="tx-new", deal_date=DEAL_DATES[2]),
        # Newer and pricier than anything in the target community, so leakage
        # would move both the recency field and the medians.
        "other_community": make_transaction(
            transaction,
            transaction_id="tx-other",
            deal_date=OTHER_DEAL_DATE,
            community_id=OTHER_COMMUNITY,
        ),
    }
    with DuckDBDatabase() as database:
        community_repository = CommunityRepository(database.connection)
        for community in communities:
            community_repository.upsert(community)
        fixture = Seeded(database.connection, stored, provider_observations)
        fixture.transaction_repository.upsert_many(stored.values())
        for key in (*TARGET_LISTING_KEYS, "foreign_community"):
            store(fixture.listing_repository, provider_observations[key])
        yield fixture


def expected_metrics(
    fixture: Seeded, community_id: str
) -> tuple[CommunityTransactionMetrics, CommunityListingMetrics]:
    """What the existing analytics produce over the same stored records."""
    return (
        compute_community_transaction_metrics(
            fixture.transaction_repository.list_for_community(community_id),
            community_id=community_id,
        ),
        compute_community_listing_metrics(
            fixture.listing_repository.list_for_community(community_id),
            fixture.listing_repository.history_for_community(community_id),
            community_id=community_id,
        ),
    )


def test_component_metrics_equal_the_existing_analytics_on_the_same_records(
    seeded: Seeded,
) -> None:
    evidence = seeded.service.get_market_evidence(TARGET_COMMUNITY)
    expected_transactions, expected_listings = expected_metrics(seeded, TARGET_COMMUNITY)

    assert evidence.community_id == TARGET_COMMUNITY
    assert evidence.transaction_metrics == expected_transactions
    assert evidence.listing_metrics == expected_listings
    # Nothing is recomputed: the components are the established models as-is.
    assert isinstance(evidence.transaction_metrics, CommunityTransactionMetrics)
    assert isinstance(evidence.listing_metrics, CommunityListingMetrics)


def test_recency_comes_from_the_stored_evidence(seeded: Seeded) -> None:
    evidence = seeded.service.get_market_evidence(TARGET_COMMUNITY)

    assert evidence.latest_deal_date == DEAL_DATES[-1]
    assert evidence.latest_snapshot_at == LATER_SNAPSHOT_AT
    # The recency fields restate the components rather than redefining them.
    assert evidence.latest_deal_date == evidence.transaction_metrics.latest_deal_date
    assert evidence.latest_snapshot_at == evidence.listing_metrics.latest_snapshot_at
    assert evidence.has_evidence


def test_empty_community_returns_valid_empty_component_metrics(seeded: Seeded) -> None:
    evidence = seeded.service.get_market_evidence("cm-does-not-exist")

    assert evidence.community_id == "cm-does-not-exist"
    assert evidence.transaction_metrics.sample_count == 0
    assert evidence.transaction_metrics.transaction_ids == ()
    assert evidence.listing_metrics.listing_count == 0
    assert evidence.listing_metrics.snapshot_count == 0
    assert evidence.listing_metrics.listing_ids == ()
    assert not evidence.has_evidence
    # Absent evidence stays absent: no imputed medians, no fabricated recency.
    assert evidence.latest_deal_date is None
    assert evidence.latest_snapshot_at is None
    assert evidence.transaction_metrics.median_unit_price_cny_sqm.value is None
    assert evidence.listing_metrics.median_active_list_price_cny.value is None
    assert evidence.listing_metrics.price_cut_share is None
    assert evidence.listing_metrics.current_status_counts == ()


def test_another_communitys_evidence_is_excluded(seeded: Seeded) -> None:
    target = seeded.service.get_market_evidence(TARGET_COMMUNITY)
    other = seeded.service.get_market_evidence(OTHER_COMMUNITY)
    foreign_listing = seeded.observations["foreign_community"].listing

    assert target.transaction_metrics.transaction_ids == ("tx-mid", "tx-new", "tx-old")
    assert "tx-other" not in target.transaction_metrics.transaction_ids
    assert target.latest_deal_date == DEAL_DATES[-1] < OTHER_DEAL_DATE
    assert foreign_listing.listing_id not in target.listing_metrics.listing_ids
    assert target.listing_metrics.listing_count == 3
    assert target.listing_metrics.snapshot_count == 4

    # The excluded evidence is stored and readable, just under its own subject.
    assert other.transaction_metrics.transaction_ids == ("tx-other",)
    assert other.listing_metrics.listing_ids == (foreign_listing.listing_id,)
    assert other.listing_metrics.snapshot_count == 1


def test_full_history_is_used_so_repricing_stays_correct(seeded: Seeded) -> None:
    evidence = seeded.service.get_market_evidence(TARGET_COMMUNITY)
    listings = evidence.listing_metrics

    # lst-fixture-0001 was observed twice and cut its asking price; the other
    # two were seen once and carry no repricing evidence at all.
    assert listings.snapshot_count == 4
    assert listings.observed_listing_count == 3
    assert listings.repricing_observable_count == 1
    assert listings.price_cut_count == 1
    assert listings.price_cut_share == 1.0
    assert listings.median_price_change_ratio.usable_count == 1
    assert listings.median_price_change_ratio.value is None

    # Latest snapshots alone would have hidden the cut entirely.
    latest_only = compute_community_listing_metrics(
        seeded.listing_repository.list_for_community(TARGET_COMMUNITY),
        seeded.listing_repository.latest_snapshots_for_community(TARGET_COMMUNITY).values(),
        community_id=TARGET_COMMUNITY,
    )
    assert latest_only.repricing_observable_count == 0
    assert latest_only.snapshot_count == 3

    # The latest observation still decides the current view.
    assert listings.current_status_count(ListingStatus.WITHDRAWN) == 1
    assert listings.active_listing_count == 1
    history = ListingQueryService(repository=seeded.listing_repository).get_listing_history(
        "lst-fixture-0001"
    )
    assert [item.snapshot_at for item in history] == [FIRST_SNAPSHOT_AT, LATER_SNAPSHOT_AT]
    assert (
        history[-1].list_price_cny - history[0].list_price_cny
    ) / history[0].list_price_cny == pytest.approx(EXPECTED_PRICE_CHANGE_RATIO)


class ReversingTransactionRepository(TransactionRepository):
    """Same rows, opposite order, to prove ordering does not reach the result."""

    def list_for_community(self, community_id: str, **kwargs: Any) -> list[Transaction]:
        return list(reversed(super().list_for_community(community_id, **kwargs)))


class ReversingListingRepository(ListingRepository):
    def list_for_community(self, community_id: str) -> list[Any]:
        return list(reversed(super().list_for_community(community_id)))

    def history_for_community(self, community_id: str) -> list[Any]:
        return list(reversed(super().history_for_community(community_id)))


def test_result_is_independent_of_repository_row_order(seeded: Seeded) -> None:
    reversed_service = build_service(
        ReversingTransactionRepository(seeded.connection),
        ReversingListingRepository(seeded.connection),
    )

    assert reversed_service.get_market_evidence(TARGET_COMMUNITY) == (
        seeded.service.get_market_evidence(TARGET_COMMUNITY)
    )
    # Repeating the same read over unchanged storage is also stable.
    assert seeded.service.get_market_evidence(TARGET_COMMUNITY) == (
        seeded.service.get_market_evidence(TARGET_COMMUNITY)
    )


def test_evidence_is_summarized_not_rewritten(seeded: Seeded) -> None:
    evidence = seeded.service.get_market_evidence(TARGET_COMMUNITY)

    # The result names the evidence; it does not copy or restate provenance.
    assert set(CommunityMarketEvidence.model_fields) == {
        "community_id",
        "transaction_metrics",
        "listing_metrics",
    }
    assert evidence.transaction_metrics.transaction_ids == tuple(
        sorted(
            item.transaction_id
            for item in seeded.transactions.values()
            if item.community_id == TARGET_COMMUNITY
        )
    )
    assert evidence.listing_metrics.listing_ids == (
        "lst-fixture-0001",
        "lst-fixture-0002",
        "lst-fixture-0003",
    )
    assert evidence.transaction_metrics.sources == ("fixture",)
    assert evidence.listing_metrics.latest_snapshot_sources == ("fixture",)

    # The canonical records keep their provenance and stay readable unchanged.
    records = TransactionQueryService(
        repository=seeded.transaction_repository
    ).get_transactions(TransactionQuery(community_id=TARGET_COMMUNITY))
    assert set(records) == {
        item for item in seeded.transactions.values() if item.community_id == TARGET_COMMUNITY
    }
    stored_history = ListingQueryService(
        repository=seeded.listing_repository
    ).get_community_listing_history(TARGET_COMMUNITY)
    assert set(stored_history) == {
        seeded.observations[key].snapshot for key in TARGET_LISTING_KEYS
    }

    # Thin evidence stays thin instead of being filled in with zeros.
    sparse = next(item for item in stored_history if item.listing_id == "lst-fixture-0003")
    assert (sparse.source_url, sparse.raw_payload_ref, sparse.unit_price_cny_sqm) == (
        None,
        None,
        None,
    )
    assert evidence.listing_metrics.median_active_unit_price_cny_sqm.value is None
    assert evidence.listing_metrics.identity_only_listing_count == 0


class CountingListingRepository(ListingRepository):
    """Records which read methods the orchestration actually calls."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        super().__init__(connection)
        self.calls: Counter[str] = Counter()

    def list_for_community(self, community_id: str) -> list[Any]:
        self.calls["list_for_community"] += 1
        return super().list_for_community(community_id)

    def latest_snapshots_for_community(self, community_id: str) -> dict[str, Any]:
        self.calls["latest_snapshots_for_community"] += 1
        return super().latest_snapshots_for_community(community_id)

    def history(self, listing_id: str) -> list[Any]:
        self.calls["history"] += 1
        return super().history(listing_id)

    def history_for_community(self, community_id: str) -> list[Any]:
        self.calls["history_for_community"] += 1
        return super().history_for_community(community_id)


def test_history_is_read_without_a_query_per_listing(
    seeded: Seeded,
    provider_observations: dict[str, ListingObservation],
) -> None:
    repository = CountingListingRepository(seeded.connection)
    service = build_service(seeded.transaction_repository, repository)

    three_listings = service.get_market_evidence(TARGET_COMMUNITY)
    assert three_listings.listing_metrics.listing_count == 3
    assert repository.calls == Counter({"list_for_community": 1, "history_for_community": 1})

    # A fourth identity must not add a fourth read.
    extra = provider_observations["valid_a"]
    store(
        repository,
        ListingObservation(
            listing=extra.listing.model_copy(
                update={"listing_id": "lst-fixture-0009", "source_listing_id": "listed-009"}
            ),
            snapshot=extra.snapshot.model_copy(update={"listing_id": "lst-fixture-0009"}),
        ),
    )
    repository.calls.clear()
    four_listings = service.get_market_evidence(TARGET_COMMUNITY)

    assert four_listings.listing_metrics.listing_count == 4
    assert repository.calls == Counter({"list_for_community": 1, "history_for_community": 1})


def test_blank_community_id_is_rejected(seeded: Seeded) -> None:
    """Metrics always name their subject, so an unnamed request cannot succeed."""
    with pytest.raises(ValueError, match="community_id"):
        seeded.service.get_market_evidence("")
    with pytest.raises(ValueError, match="community_id must not be blank"):
        seeded.service.get_market_evidence("   ")


def test_mismatched_component_metrics_are_rejected(seeded: Seeded) -> None:
    """Evidence of two communities cannot be assembled into one result."""
    target = seeded.service.get_market_evidence(TARGET_COMMUNITY)
    other = seeded.service.get_market_evidence(OTHER_COMMUNITY)

    with pytest.raises(ValueError, match="must describe community"):
        CommunityMarketEvidence(
            community_id=TARGET_COMMUNITY,
            transaction_metrics=target.transaction_metrics,
            listing_metrics=other.listing_metrics,
        )


def test_orchestration_is_source_and_acquisition_independent() -> None:
    """The service must not reach below the storage/analytics boundary."""
    source = Path(community_evidence.__file__).read_text(encoding="utf-8")
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
        "cn_property_agent.services.listing_query",
        "cn_property_agent.services.transaction_query",
    }
    forbidden = (
        "lianjia",
        "beike",
        "cn_property_agent.config",
        "cn_property_agent.providers",
        "cn_property_agent.jobs",
        "httpx",
        "requests",
        "playwright",
        "datetime.now",
        "utcnow",
    )
    assert not any(token in source.lower() for token in forbidden)
