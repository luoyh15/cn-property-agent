from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path
from types import ModuleType

import pytest

from cn_property_agent.analytics import (
    MINIMUM_SAMPLE_COUNT,
    ListingStatusCount,
    common,
    compute_community_listing_metrics,
    listing_metrics,
)
from cn_property_agent.domain import Listing, ListingObservation, ListingSnapshot, ListingStatus

COMMUNITY = "cm-sh-pd-002"
OTHER_COMMUNITY = "cm-sh-mh-001"
AREA_SQM = 100.0

FIRST_SEEN = datetime(2026, 8, 1, tzinfo=timezone.utc)
MIDDLE = datetime(2026, 8, 15, tzinfo=timezone.utc)
LATEST = datetime(2026, 9, 1, tzinfo=timezone.utc)

ACTIVE = ListingStatus.ACTIVE
WITHDRAWN = ListingStatus.WITHDRAWN

# Six identities in one community. Areas are 100 sqm, so unit price is the
# asking price divided by 100 and every number below can be checked by hand.
#
#   listing  first ask   latest ask   latest status  ratio   active unit price
#   lst-1      10.0M         9.0M        active      -0.10        90_000
#   lst-2      10.0M        11.0M        active      +0.10       110_000
#   lst-3       8.0M         8.0M        active       0.00        80_000
#   lst-4      12.0M         9.6M        withdrawn   -0.20             -
#   lst-5      20.0M            -        active          -   (not reported)
#   lst-6   identity only, never observed
#
#   active asking prices  8.0M  9.0M  11.0M  20.0M -> median 10.0M of 4
#   active unit prices  80_000 90_000 110_000      -> median 90_000 of 3
#   change ratios       -0.20 -0.10  0.00  +0.10   -> median -0.05 of 4
SAMPLE: tuple[tuple[str, tuple[tuple[datetime, float, ListingStatus], ...]], ...] = (
    ("lst-1", ((FIRST_SEEN, 10_000_000.0, ACTIVE), (LATEST, 9_000_000.0, ACTIVE))),
    ("lst-2", ((FIRST_SEEN, 10_000_000.0, ACTIVE), (LATEST, 11_000_000.0, ACTIVE))),
    ("lst-3", ((FIRST_SEEN, 8_000_000.0, ACTIVE), (LATEST, 8_000_000.0, ACTIVE))),
    ("lst-4", ((FIRST_SEEN, 12_000_000.0, ACTIVE), (LATEST, 9_600_000.0, WITHDRAWN))),
    ("lst-5", ((MIDDLE, 20_000_000.0, ACTIVE),)),
    ("lst-6", ()),
)
# The one observation that carries no unit price, so the two active medians
# summarize different numbers of records.
WITHOUT_UNIT_PRICE = frozenset({"lst-5"})


def make_listing(base: Listing, listing_id: str, *, community_id: str = COMMUNITY) -> Listing:
    """Vary only identity; the quasi-static descriptors stay as fixtured."""
    return base.model_copy(
        update={
            "listing_id": listing_id,
            "community_id": community_id,
            "source_listing_id": f"listed-{listing_id}",
            "area_sqm": AREA_SQM,
            "first_seen_at": FIRST_SEEN,
            "last_seen_at": LATEST,
            "status": ACTIVE,
        }
    )


def make_snapshot(
    base: ListingSnapshot,
    listing_id: str,
    *,
    snapshot_at: datetime,
    list_price_cny: float,
    status: ListingStatus = ACTIVE,
) -> ListingSnapshot:
    """Vary only what the metrics read; provenance stays as fixtured."""
    unit_price = None if listing_id in WITHOUT_UNIT_PRICE else list_price_cny / AREA_SQM
    return base.model_copy(
        update={
            "listing_id": listing_id,
            "snapshot_at": snapshot_at,
            "list_price_cny": list_price_cny,
            "unit_price_cny_sqm": unit_price,
            "status": status,
        }
    )


@pytest.fixture
def observation(provider_observations: dict[str, ListingObservation]) -> ListingObservation:
    return provider_observations["valid_a"]


@pytest.fixture
def listings(observation: ListingObservation) -> tuple[Listing, ...]:
    return tuple(make_listing(observation.listing, listing_id) for listing_id, _ in SAMPLE)


@pytest.fixture
def snapshots(observation: ListingObservation) -> tuple[ListingSnapshot, ...]:
    return tuple(
        make_snapshot(
            observation.snapshot,
            listing_id,
            snapshot_at=snapshot_at,
            list_price_cny=price,
            status=status,
        )
        for listing_id, history in SAMPLE
        for snapshot_at, price, status in history
    )


def test_no_listings_is_explicit_absence_not_zero() -> None:
    metrics = compute_community_listing_metrics((), (), community_id=COMMUNITY)

    assert metrics.community_id == COMMUNITY
    assert metrics.minimum_sample_count == MINIMUM_SAMPLE_COUNT
    assert not metrics.has_listings
    assert metrics.listing_count == metrics.snapshot_count == 0
    assert metrics.observed_listing_count == metrics.identity_only_listing_count == 0
    assert metrics.latest_snapshot_at is None
    assert metrics.current_status_counts == ()
    assert metrics.current_status_count(ACTIVE) == 0
    assert metrics.active_listing_count == 0
    assert metrics.repricing_observable_count == 0
    assert metrics.price_cut_share is None
    assert metrics.listing_ids == ()
    assert metrics.latest_snapshot_sources == ()
    for metric in (
        metrics.median_active_list_price_cny,
        metrics.median_active_unit_price_cny_sqm,
        metrics.median_price_change_ratio,
    ):
        assert metric.value is None
        assert metric.usable_count == 0


def test_inventory_and_status_accounting(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert metrics.listing_count == 6
    assert metrics.snapshot_count == 9
    assert metrics.observed_listing_count == 5
    assert metrics.active_listing_count == 4
    assert metrics.latest_snapshot_at == LATEST
    # Statuses come off the latest observation of each observed listing only.
    assert metrics.current_status_counts == (
        ListingStatusCount(status=ACTIVE, count=4),
        ListingStatusCount(status=WITHDRAWN, count=1),
    )
    assert sum(item.count for item in metrics.current_status_counts) == 5
    assert metrics.current_status_count(WITHDRAWN) == 1
    assert metrics.current_status_count(ListingStatus.SOLD) == 0


def test_identity_only_listing_is_visible_without_a_fabricated_status(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    """lst-6 has an active identity but no observation; it stays uncounted."""
    identity_only = next(item for item in listings if item.listing_id == "lst-6")
    assert identity_only.status is ACTIVE

    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert "lst-6" in metrics.listing_ids
    assert metrics.identity_only_listing_count == 1
    assert metrics.listing_count - metrics.observed_listing_count == 1
    # It is not counted as active, not given a price, and cannot be repriced.
    assert metrics.active_listing_count == 4
    assert metrics.median_active_list_price_cny.usable_count == 4
    assert metrics.repricing_observable_count == 4


def test_evidence_ids_and_sources_are_exposed(
    listings: tuple[Listing, ...],
    snapshots: tuple[ListingSnapshot, ...],
    observation: ListingObservation,
) -> None:
    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert metrics.listing_ids == ("lst-1", "lst-2", "lst-3", "lst-4", "lst-5", "lst-6")
    assert metrics.latest_snapshot_sources == (observation.snapshot.source,)


def test_latest_snapshot_is_chosen_by_timestamp_not_input_order(
    observation: ListingObservation,
) -> None:
    listing = make_listing(observation.listing, "lst-1")
    earlier = make_snapshot(
        observation.snapshot, "lst-1", snapshot_at=FIRST_SEEN, list_price_cny=10_000_000.0
    )
    later = make_snapshot(
        observation.snapshot,
        "lst-1",
        snapshot_at=LATEST,
        list_price_cny=9_000_000.0,
        status=WITHDRAWN,
    )

    chronological = compute_community_listing_metrics(
        (listing,), (earlier, later), community_id=COMMUNITY, minimum_sample_count=1
    )
    reversed_input = compute_community_listing_metrics(
        (listing,), (later, earlier), community_id=COMMUNITY, minimum_sample_count=1
    )

    assert reversed_input == chronological
    assert chronological.latest_snapshot_at == LATEST
    assert chronological.current_status_count(WITHDRAWN) == 1
    assert chronological.active_listing_count == 0
    assert chronological.median_price_change_ratio.value == pytest.approx(-0.1)


def test_active_price_medians_use_their_own_usable_counts(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert metrics.median_active_list_price_cny.value == pytest.approx(10_000_000.0)
    assert metrics.median_active_list_price_cny.usable_count == 4
    # Only three of the four active listings were observed with a unit price.
    assert metrics.median_active_unit_price_cny_sqm.value == pytest.approx(90_000.0)
    assert metrics.median_active_unit_price_cny_sqm.usable_count == 3


def test_withdrawn_listing_is_excluded_from_the_active_medians(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    """lst-4 asks 9.6M, which would move both medians if status were ignored."""
    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert metrics.median_active_list_price_cny.usable_count == 4
    assert metrics.median_active_list_price_cny.value == pytest.approx(10_000_000.0)


def test_thin_evidence_reports_none_rather_than_zero(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    strict = compute_community_listing_metrics(
        listings, snapshots, community_id=COMMUNITY, minimum_sample_count=5
    )

    assert strict.minimum_sample_count == 5
    # The counts are facts and stay; only the summarizing medians are withheld.
    assert strict.active_listing_count == 4
    assert strict.repricing_observable_count == 4
    assert strict.price_cut_count == 2
    assert strict.price_cut_share == pytest.approx(0.5)
    for metric in (
        strict.median_active_list_price_cny,
        strict.median_active_unit_price_cny_sqm,
        strict.median_price_change_ratio,
    ):
        assert metric.value is None
        assert not metric.has_value
    assert strict.median_active_list_price_cny.usable_count == 4
    assert strict.median_active_unit_price_cny_sqm.usable_count == 3


def test_price_cut_increase_and_unchanged_are_classified(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    metrics = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert metrics.repricing_observable_count == 4
    assert (metrics.price_cut_count, metrics.price_increase_count) == (2, 1)
    assert metrics.unchanged_price_count == 1
    assert (
        metrics.price_cut_count + metrics.price_increase_count + metrics.unchanged_price_count
        == metrics.repricing_observable_count
    )
    assert metrics.price_cut_share == pytest.approx(0.5)
    assert metrics.median_price_change_ratio.value == pytest.approx(-0.05)
    assert metrics.median_price_change_ratio.usable_count == 4


@pytest.mark.parametrize(
    ("earliest", "latest", "expected"),
    [
        (10_000_000.0, 9_000_000.0, -0.1),
        (10_000_000.0, 12_500_000.0, 0.25),
        (8_000_000.0, 8_000_000.0, 0.0),
        (4_000_000.0, 10_000_000.0, 1.5),
    ],
)
def test_price_change_ratio_sign_and_value(
    observation: ListingObservation, earliest: float, latest: float, expected: float
) -> None:
    """A cut is negative, an increase positive, and large moves are not clamped."""
    listing = make_listing(observation.listing, "lst-1")
    history = (
        make_snapshot(
            observation.snapshot, "lst-1", snapshot_at=FIRST_SEEN, list_price_cny=earliest
        ),
        make_snapshot(observation.snapshot, "lst-1", snapshot_at=LATEST, list_price_cny=latest),
    )

    metrics = compute_community_listing_metrics(
        (listing,), history, community_id=COMMUNITY, minimum_sample_count=1
    )

    assert metrics.median_price_change_ratio.value == pytest.approx(expected)
    assert metrics.price_cut_count == (1 if expected < 0 else 0)
    assert metrics.price_increase_count == (1 if expected > 0 else 0)
    assert metrics.unchanged_price_count == (1 if expected == 0 else 0)


def test_only_the_first_and_latest_asking_price_are_compared(
    observation: ListingObservation,
) -> None:
    """A cut that is later restored reads as unchanged, not as a cut."""
    listing = make_listing(observation.listing, "lst-1")
    history = tuple(
        make_snapshot(
            observation.snapshot, "lst-1", snapshot_at=snapshot_at, list_price_cny=price
        )
        for snapshot_at, price in (
            (FIRST_SEEN, 10_000_000.0),
            (MIDDLE, 9_000_000.0),
            (LATEST, 10_000_000.0),
        )
    )

    metrics = compute_community_listing_metrics(
        (listing,), history, community_id=COMMUNITY, minimum_sample_count=1
    )

    assert metrics.snapshot_count == 3
    assert metrics.repricing_observable_count == 1
    assert metrics.unchanged_price_count == 1
    assert metrics.price_cut_count == 0
    assert metrics.median_price_change_ratio.value == pytest.approx(0.0)


def test_a_listing_seen_once_is_not_in_the_repricing_denominator(
    observation: ListingObservation,
) -> None:
    listing = make_listing(observation.listing, "lst-5")
    only_snapshot = make_snapshot(
        observation.snapshot, "lst-5", snapshot_at=MIDDLE, list_price_cny=20_000_000.0
    )

    metrics = compute_community_listing_metrics(
        (listing,), (only_snapshot,), community_id=COMMUNITY, minimum_sample_count=1
    )

    assert metrics.observed_listing_count == 1
    assert metrics.active_listing_count == 1
    assert metrics.repricing_observable_count == 0
    assert metrics.unchanged_price_count == 0
    assert metrics.price_cut_share is None
    assert metrics.median_price_change_ratio.value is None
    assert metrics.median_price_change_ratio.usable_count == 0


def test_listings_from_another_community_are_an_error(
    listings: tuple[Listing, ...],
    snapshots: tuple[ListingSnapshot, ...],
    observation: ListingObservation,
) -> None:
    foreign = make_listing(observation.listing, "lst-foreign", community_id=OTHER_COMMUNITY)

    with pytest.raises(ValueError, match=OTHER_COMMUNITY):
        compute_community_listing_metrics(
            (*listings, foreign), snapshots, community_id=COMMUNITY
        )


def test_a_snapshot_of_an_unknown_listing_is_an_error(
    listings: tuple[Listing, ...],
    snapshots: tuple[ListingSnapshot, ...],
    observation: ListingObservation,
) -> None:
    orphan = make_snapshot(
        observation.snapshot, "lst-unknown", snapshot_at=LATEST, list_price_cny=7_000_000.0
    )

    with pytest.raises(ValueError, match="lst-unknown"):
        compute_community_listing_metrics(
            listings, (*snapshots, orphan), community_id=COMMUNITY
        )


def test_a_repeated_listing_identity_is_an_error(listings: tuple[Listing, ...]) -> None:
    with pytest.raises(ValueError, match="lst-1"):
        compute_community_listing_metrics((*listings, listings[0]), (), community_id=COMMUNITY)


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
        compute_community_listing_metrics((), (), **kwargs)


def test_repeated_calls_and_input_order_do_not_change_the_result(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    expected = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    repeated = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    assert repeated == expected
    assert (
        compute_community_listing_metrics(
            tuple(reversed(listings)), tuple(reversed(snapshots)), community_id=COMMUNITY
        )
        == expected
    )
    rotated = (*listings[3:], *listings[:3])
    interleaved = tuple(snapshots[1::2]) + tuple(snapshots[0::2])
    assert (
        compute_community_listing_metrics(rotated, interleaved, community_id=COMMUNITY) == expected
    )


def test_every_permutation_of_one_listing_history_gives_the_same_result(
    observation: ListingObservation,
) -> None:
    listing = make_listing(observation.listing, "lst-1")
    history = tuple(
        make_snapshot(
            observation.snapshot, "lst-1", snapshot_at=snapshot_at, list_price_cny=price
        )
        for snapshot_at, price in (
            (FIRST_SEEN, 10_000_000.0),
            (MIDDLE, 9_500_000.0),
            (LATEST, 9_000_000.0),
        )
    )
    expected = compute_community_listing_metrics(
        (listing,), history, community_id=COMMUNITY, minimum_sample_count=1
    )

    for ordering in permutations(history):
        assert (
            compute_community_listing_metrics(
                (listing,), ordering, community_id=COMMUNITY, minimum_sample_count=1
            )
            == expected
        )
    assert expected.median_price_change_ratio.value == pytest.approx(-0.1)


def test_disagreeing_observations_of_one_instant_are_an_error(
    observation: ListingObservation,
) -> None:
    """Two prices for one instant say nothing about which came later."""
    listing = make_listing(observation.listing, "lst-1")
    conflict = tuple(
        make_snapshot(
            observation.snapshot, "lst-1", snapshot_at=FIRST_SEEN + offset, list_price_cny=price
        )
        for offset, price in (
            (timedelta(0), 10_000_000.0),
            (timedelta(days=30), 9_000_000.0),
            (timedelta(days=30), 9_100_000.0),
        )
    )

    for ordering in permutations(conflict):
        with pytest.raises(ValueError, match=r"lst-1' at 2026-08-31"):
            compute_community_listing_metrics(
                (listing,), ordering, community_id=COMMUNITY, minimum_sample_count=1
            )


def test_a_repeated_observation_is_not_a_second_observation(
    observation: ListingObservation,
) -> None:
    """The same instant seen twice is one observation, not a repricing."""
    listing = make_listing(observation.listing, "lst-5")
    only_snapshot = make_snapshot(
        observation.snapshot, "lst-5", snapshot_at=MIDDLE, list_price_cny=20_000_000.0
    )

    metrics = compute_community_listing_metrics(
        (listing,),
        (only_snapshot, only_snapshot),
        community_id=COMMUNITY,
        minimum_sample_count=1,
    )

    assert metrics.snapshot_count == 1
    assert metrics.observed_listing_count == 1
    assert metrics.repricing_observable_count == 0
    assert metrics.median_price_change_ratio.value is None
    assert metrics.median_price_change_ratio.usable_count == 0


def test_repeated_observations_leave_a_history_unchanged(
    listings: tuple[Listing, ...], snapshots: tuple[ListingSnapshot, ...]
) -> None:
    expected = compute_community_listing_metrics(listings, snapshots, community_id=COMMUNITY)

    duplicated = compute_community_listing_metrics(
        listings, (*snapshots, *reversed(snapshots)), community_id=COMMUNITY
    )

    assert duplicated == expected
    assert duplicated.snapshot_count == 9


@pytest.mark.parametrize(
    ("module", "expected_internal"),
    [
        (listing_metrics, {"cn_property_agent.analytics.common", "cn_property_agent.domain"}),
        # The shared vocabulary is checked too: allowing listing_metrics to
        # import it would otherwise leave a way in for everything it imports.
        (common, {"cn_property_agent.domain"}),
    ],
)
def test_analytics_is_source_independent(module: ModuleType, expected_internal: set[str]) -> None:
    """Analytics may see canonical records only, never where they came from."""
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    internal = {name for name in imported if name.startswith("cn_property_agent")}
    assert internal == expected_internal
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
