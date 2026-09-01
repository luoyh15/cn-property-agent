from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from statistics import median

from pydantic import Field

from cn_property_agent.domain import FrozenModel, Transaction

MINIMUM_SAMPLE_COUNT = 3
"""Fewest usable records a median may summarize.

Below three values a median is either a single observation or the midpoint of a
pair, which reads like a market level while describing almost nothing. This is
the only threshold in this module; callers that know their evidence base may
raise it, and metrics that fall short report ``None`` plus their usable count
rather than a number that looks precise.
"""


class MedianMetric(FrozenModel):
    """A median over the records that actually carry the underlying field.

    ``usable_count`` is the number of records the metric could be computed
    from, which for an optional field is at most the overall sample count.
    ``value`` is ``None`` whenever that count is below the configured minimum:
    missing or thin evidence is never imputed, and never reported as zero.
    """

    value: float | None = None
    usable_count: int = Field(default=0, ge=0)

    @property
    def has_value(self) -> bool:
        return self.value is not None


class CommunityTransactionMetrics(FrozenModel):
    """Deterministic baseline statistics for one community's transactions.

    ``sample_count`` and ``latest_deal_date`` describe the evidence itself and
    are reported whenever any record exists, so a caller can always tell "no
    deals" from "few deals" from "a computed median". Every median carries its
    own usable count; see :class:`MedianMetric`.

    ``transaction_ids`` and ``sources`` keep the underlying evidence
    identifiable. Analytics summarizes canonical values, it does not restate or
    replace the provenance stored with each record.
    """

    community_id: str = Field(min_length=1)
    sample_count: int = Field(ge=0)
    minimum_sample_count: int = Field(ge=1)
    latest_deal_date: date | None = None
    median_unit_price_cny_sqm: MedianMetric = MedianMetric()
    median_deal_price_cny: MedianMetric = MedianMetric()
    median_days_on_market_days: MedianMetric = MedianMetric()
    median_negotiation_discount: MedianMetric = MedianMetric()
    transaction_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    @property
    def has_transactions(self) -> bool:
        return self.sample_count > 0

    @property
    def has_sufficient_evidence(self) -> bool:
        """Whether the sample supports the medians over always-present fields."""
        return self.sample_count >= self.minimum_sample_count


def compute_community_transaction_metrics(
    transactions: Iterable[Transaction],
    *,
    community_id: str,
    minimum_sample_count: int = MINIMUM_SAMPLE_COUNT,
) -> CommunityTransactionMetrics:
    """Summarize canonical transactions for one community.

    Pure and deterministic: no clock, no I/O, no source-specific handling. The
    result depends only on the set of records passed in, not on their order, so
    repeated calls over the same evidence return equal values.

    ``community_id`` is required rather than inferred, so an empty sample still
    names its subject. A record belonging to another community is an error, not
    something to filter away silently.

    No recent-versus-prior period comparison is computed here: it would need a
    reference date and window length that the canonical record set does not
    supply on its own, and an implicit choice of either would not be an
    unambiguous deterministic definition.
    """
    minimum = _validate_minimum_sample_count(minimum_sample_count)
    if not community_id.strip():
        raise ValueError("community_id must not be blank")

    sample = tuple(transactions)
    foreign = sorted({item.community_id for item in sample if item.community_id != community_id})
    if foreign:
        raise ValueError(
            f"transactions must all belong to community {community_id!r},"
            f" also got: {', '.join(repr(item) for item in foreign)}"
        )

    return CommunityTransactionMetrics(
        community_id=community_id,
        sample_count=len(sample),
        minimum_sample_count=minimum,
        latest_deal_date=max((item.deal_date for item in sample), default=None),
        median_unit_price_cny_sqm=_median_metric(
            [item.unit_price_cny_sqm for item in sample], minimum
        ),
        median_deal_price_cny=_median_metric([item.deal_price_cny for item in sample], minimum),
        median_days_on_market_days=_median_metric(
            [float(item.days_on_market) for item in sample if item.days_on_market is not None],
            minimum,
        ),
        median_negotiation_discount=_median_metric(
            [
                _negotiation_discount(item.initial_listing_price_cny, item.deal_price_cny)
                for item in sample
                if item.initial_listing_price_cny is not None
            ],
            minimum,
        ),
        transaction_ids=tuple(sorted(item.transaction_id for item in sample)),
        sources=tuple(sorted({item.source for item in sample})),
    )


def _negotiation_discount(initial_listing_price_cny: float, deal_price_cny: float) -> float:
    """Share of the initial asking price given up at the deal.

    Both prices are validated positive by the canonical model, so no record is
    skipped for arithmetic reasons. A deal above the initial asking price gives
    a negative discount; that is an observation, not an error to clamp away.
    """
    return (initial_listing_price_cny - deal_price_cny) / initial_listing_price_cny


def _median_metric(values: Sequence[float], minimum_sample_count: int) -> MedianMetric:
    if len(values) < minimum_sample_count:
        return MedianMetric(usable_count=len(values))
    return MedianMetric(value=float(median(values)), usable_count=len(values))


def _validate_minimum_sample_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"minimum_sample_count must be an integer, got {value!r}")
    if value < 1:
        raise ValueError(f"minimum_sample_count must be at least 1, got {value!r}")
    return value
