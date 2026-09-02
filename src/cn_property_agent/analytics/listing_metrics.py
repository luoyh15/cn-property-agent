"""Deterministic inventory and repricing metrics over canonical listing evidence.

Two canonical tables answer two different questions and are kept apart here for
the same reason ingestion keeps them apart:

- a :class:`~cn_property_agent.domain.Listing` is a stable identity, and its
  count answers "how many units of this community have ever been seen listed";
- a :class:`~cn_property_agent.domain.ListingSnapshot` is one observation of
  that identity, and a listing's snapshot history answers "what is it asking
  now, and what did it ask first".

Everything below is derived from those two inputs and nothing else. There is no
clock: "current" means *the latest observation supplied*, never "as of today",
so a stale evidence set produces stale-but-honest numbers rather than metrics
that silently depend on when they were computed. There is likewise no composite
seller-pressure score: this module reports counts, shares and medians a reader
can recompute by hand, and leaves the weighting of those facts to a later
assessment layer that can state its own semantics.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from pydantic import AwareDatetime, Field

from cn_property_agent.analytics.common import (
    MINIMUM_SAMPLE_COUNT,
    MedianMetric,
    median_metric,
    validate_community_id,
    validate_minimum_sample_count,
)
from cn_property_agent.domain import FrozenModel, Listing, ListingSnapshot, ListingStatus


class ListingStatusCount(FrozenModel):
    """How many listings the latest observation places in one status."""

    status: ListingStatus
    count: int = Field(ge=0)


class CommunityListingMetrics(FrozenModel):
    """Deterministic inventory and repricing statistics for one community.

    The evidence counts come first and are always reported, so a caller can
    tell "nothing listed" from "listed but never re-observed" from "a computed
    median". In particular:

    - ``listing_count`` counts canonical identities, whether or not any
      observation of them was supplied;
    - ``identity_only_listing_count`` counts the identities with no observation
      at all. They are visible in ``listing_count`` and ``listing_ids`` but
      contribute no status, price or repricing evidence, because inventing a
      status for them would turn missing data into a market fact;
    - ``current_status_counts`` reads each listing's status off its latest
      observation only. Statuses nobody is in are omitted; use
      :meth:`current_status_count`, which reports the absent ones as zero.

    The repricing block compares the earliest and latest asking price stored
    for a listing, so it describes only listings observed at least twice
    (``repricing_observable_count``). A listing seen once carries no evidence
    about repricing and is excluded from that denominator rather than counted
    as unchanged. ``price_cut_share`` is that denominator's exact composition
    and is reported whenever it is non-empty, ``None`` otherwise;
    ``minimum_sample_count`` gates the medians, which claim to describe a
    distribution, not the counts and shares, which only restate the evidence.

    ``listing_ids`` and ``latest_snapshot_sources`` keep the underlying
    evidence identifiable. Analytics summarizes canonical values, it does not
    restate or replace the provenance stored with each observation.
    """

    community_id: str = Field(min_length=1)
    minimum_sample_count: int = Field(ge=1)
    listing_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)
    observed_listing_count: int = Field(ge=0)
    identity_only_listing_count: int = Field(ge=0)
    latest_snapshot_at: AwareDatetime | None = None
    current_status_counts: tuple[ListingStatusCount, ...] = ()
    active_listing_count: int = Field(default=0, ge=0)
    median_active_list_price_cny: MedianMetric = MedianMetric()
    median_active_unit_price_cny_sqm: MedianMetric = MedianMetric()
    repricing_observable_count: int = Field(default=0, ge=0)
    price_cut_count: int = Field(default=0, ge=0)
    price_increase_count: int = Field(default=0, ge=0)
    unchanged_price_count: int = Field(default=0, ge=0)
    price_cut_share: float | None = None
    median_price_change_ratio: MedianMetric = MedianMetric()
    listing_ids: tuple[str, ...] = ()
    latest_snapshot_sources: tuple[str, ...] = ()

    @property
    def has_listings(self) -> bool:
        return self.listing_count > 0

    def current_status_count(self, status: ListingStatus) -> int:
        """Listings whose latest observation carries ``status``, zero if none."""
        for item in self.current_status_counts:
            if item.status is status:
                return item.count
        return 0


def compute_community_listing_metrics(
    listings: Iterable[Listing],
    snapshots: Iterable[ListingSnapshot],
    *,
    community_id: str,
    minimum_sample_count: int = MINIMUM_SAMPLE_COUNT,
) -> CommunityListingMetrics:
    """Summarize canonical listing identities and their history for one community.

    Pure and deterministic: no clock, no I/O, no source-specific handling. The
    result depends only on the sets of records passed in, not on their order,
    so repeated calls over the same evidence return equal values.

    ``community_id`` is required rather than inferred, so an empty community
    still names its subject. Evidence that does not belong to the requested
    community, or that cannot be attached to a supplied identity, is an error
    rather than something to filter away silently:

    - a listing of another community is rejected, naming the foreign ids;
    - a repeated ``listing_id`` is rejected, because two identities cannot be
      told apart and their histories would silently merge;
    - a snapshot whose ``listing_id`` was not supplied is rejected, because
      dropping it would understate exactly the inventory it evidences.

    Snapshots attach to listings through ``listing_id`` alone. Within one
    listing they are ordered by ``snapshot_at``; observations sharing a
    timestamp are ordered by their canonical content, never by input position,
    so no metric depends on the order in which evidence was collected.
    """
    minimum = validate_minimum_sample_count(minimum_sample_count)
    validate_community_id(community_id)

    identities = _index_listings(listings, community_id)
    history = _group_snapshots(snapshots, identities)
    latest = tuple(item[-1] for item in history.values())
    active = tuple(item for item in latest if item.status is ListingStatus.ACTIVE)
    changes = tuple(_price_change_ratio(item) for item in history.values() if len(item) > 1)
    cuts = sum(1 for ratio in changes if ratio < 0)
    increases = sum(1 for ratio in changes if ratio > 0)

    return CommunityListingMetrics(
        community_id=community_id,
        minimum_sample_count=minimum,
        listing_count=len(identities),
        snapshot_count=sum(len(item) for item in history.values()),
        observed_listing_count=len(history),
        identity_only_listing_count=len(identities) - len(history),
        latest_snapshot_at=max((item.snapshot_at for item in latest), default=None),
        current_status_counts=_status_counts(latest),
        active_listing_count=len(active),
        median_active_list_price_cny=median_metric(
            [item.list_price_cny for item in active], minimum
        ),
        median_active_unit_price_cny_sqm=median_metric(
            [item.unit_price_cny_sqm for item in active if item.unit_price_cny_sqm is not None],
            minimum,
        ),
        repricing_observable_count=len(changes),
        price_cut_count=cuts,
        price_increase_count=increases,
        unchanged_price_count=len(changes) - cuts - increases,
        price_cut_share=(cuts / len(changes)) if changes else None,
        median_price_change_ratio=median_metric(changes, minimum),
        listing_ids=tuple(sorted(identities)),
        latest_snapshot_sources=tuple(sorted({item.source for item in latest})),
    )


def _price_change_ratio(snapshots: tuple[ListingSnapshot, ...]) -> float:
    """Asking-price move from a listing's first observation to its latest.

    ``(latest - earliest) / earliest``: a cut is negative, an increase is
    positive, and an unmoved asking price is exactly zero. Only the two ends of
    the history are compared, so a listing that cut and then restored its price
    reads as unchanged; counting individual moves needs a separate metric.

    Asking prices are validated positive by the canonical model, so no listing
    is skipped for arithmetic reasons, and valid values are never clamped.
    """
    earliest, latest = snapshots[0].list_price_cny, snapshots[-1].list_price_cny
    return (latest - earliest) / earliest


def _status_counts(latest: tuple[ListingSnapshot, ...]) -> tuple[ListingStatusCount, ...]:
    """Status tally in canonical enum order, omitting statuses nobody is in."""
    counts = Counter(item.status for item in latest)
    return tuple(
        ListingStatusCount(status=status, count=counts[status])
        for status in ListingStatus
        if counts[status]
    )


def _index_listings(listings: Iterable[Listing], community_id: str) -> dict[str, Listing]:
    identities: dict[str, Listing] = {}
    foreign: set[str] = set()
    repeated: set[str] = set()
    for listing in listings:
        if listing.community_id != community_id:
            foreign.add(listing.community_id)
        if listing.listing_id in identities:
            repeated.add(listing.listing_id)
        identities[listing.listing_id] = listing

    if foreign:
        raise ValueError(
            f"listings must all belong to community {community_id!r},"
            f" also got: {_render(foreign)}"
        )
    if repeated:
        raise ValueError(f"each listing must be supplied once, got repeated: {_render(repeated)}")
    return identities


def _group_snapshots(
    snapshots: Iterable[ListingSnapshot], identities: Mapping[str, Listing]
) -> dict[str, tuple[ListingSnapshot, ...]]:
    grouped: dict[str, list[ListingSnapshot]] = {}
    unknown: set[str] = set()
    for snapshot in snapshots:
        if snapshot.listing_id not in identities:
            unknown.add(snapshot.listing_id)
            continue
        grouped.setdefault(snapshot.listing_id, []).append(snapshot)

    if unknown:
        raise ValueError(
            f"snapshots must belong to a supplied listing, got unknown: {_render(unknown)}"
        )
    return {
        listing_id: tuple(sorted(items, key=_snapshot_order))
        for listing_id, items in sorted(grouped.items())
    }


def _snapshot_order(snapshot: ListingSnapshot) -> tuple[AwareDatetime, str]:
    """Chronological, with canonical content deciding same-instant ties.

    Two observations of one listing at the same instant carry no evidence about
    which came later, so neither may win by being passed in first.
    """
    return snapshot.snapshot_at, snapshot.model_dump_json()


def _render(values: set[str]) -> str:
    return ", ".join(repr(value) for value in sorted(values))
