"""Shared vocabulary for deterministic metric computation.

Every metric module summarizes canonical records the same way: a median is
reported only when enough records actually carry the underlying field, the
subject community is named explicitly rather than inferred, and thin evidence
is reported as ``None`` plus a usable count instead of a fabricated zero. These
rules live here so the transaction and listing views cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from pydantic import Field

from cn_property_agent.domain import FrozenModel

MINIMUM_SAMPLE_COUNT = 3
"""Fewest usable records a median may summarize.

Below three values a median is either a single observation or the midpoint of a
pair, which reads like a market level while describing almost nothing. This is
the only threshold in analytics; callers that know their evidence base may raise
it, and metrics that fall short report ``None`` plus their usable count rather
than a number that looks precise.
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


def median_metric(values: Sequence[float], minimum_sample_count: int) -> MedianMetric:
    if len(values) < minimum_sample_count:
        return MedianMetric(usable_count=len(values))
    return MedianMetric(value=float(median(values)), usable_count=len(values))


def validate_minimum_sample_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"minimum_sample_count must be an integer, got {value!r}")
    if value < 1:
        raise ValueError(f"minimum_sample_count must be at least 1, got {value!r}")
    return value


def validate_community_id(value: str) -> str:
    """Metrics always name their subject, including when the sample is empty."""
    if not value.strip():
        raise ValueError("community_id must not be blank")
    return value
