"""Source-independent read access to stored official market observations.

Community evidence answers "what happened in this community"; a market
observation answers "what did the official series say about the geography that
community sits in". This module is the read side of the second question only:
it returns canonical records that acquisition has already persisted, so a
benchmark comparison can later be computed against evidence that is stored,
attributable and reproducible.

Nothing here contacts a provider. A city that was never ingested reads as
empty rather than triggering a fetch, and no geography or metric name is
special: both are ordinary data supplied by the caller.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from cn_property_agent.domain import MarketObservation
from cn_property_agent.storage.repositories import MarketObservationRepository


class MarketObservationQuery(BaseModel):
    """One read request over canonical market observations.

    ``city_code`` is the required subject boundary. The other filters are
    optional narrowings, and omitting one means "do not filter on this field";
    it never means "match rows whose field is NULL", so observations without a
    ``geography_code`` are reachable through the unfiltered query rather than
    by asking for a null code.

    The date bounds are inclusive and independently optional, as in
    :class:`~cn_property_agent.services.transaction_query.TransactionQuery`, and
    they constrain the observed period itself: ``start_date`` bounds
    ``period_start`` from below and ``end_date`` bounds ``period_end`` from
    above, so a window selects the observations wholly inside it.

    Every string filter is validated here rather than left to SQL: a blank
    value is a mistake in the caller, not a filter that happens to match
    nothing, so it is rejected at construction time in both the required and
    the optional case.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    city_code: str = Field(min_length=1)
    geography_type: str | None = None
    geography_code: str | None = None
    metric_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("city_code")
    @classmethod
    def validate_city_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("city_code must not be blank")
        return value

    @field_validator("geography_type", "geography_code", "metric_name")
    @classmethod
    def validate_optional_filter(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(f"{info.field_name} must not be blank when provided")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "MarketObservationQuery":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class MarketObservationQueryService:
    """Read stored canonical market observations by city, geography and metric.

    Semantics:

    - results are restricted to the requested ``city_code``; another city's
      observations are excluded even when they share a metric name, a source or
      a period;
    - ordering is chronological — ``period_start``, then ``period_end``, then
      ``observation_id`` — which is a total order because ``observation_id`` is
      the storage primary key, so repeated identical queries return an
      identical sequence;
    - each observation appears at most once however often it was ingested,
      because ``observation_id`` is that primary key;
    - no matching rows is an empty tuple, which is a success rather than an
      error or an acquisition attempt;
    - invalid input raises ``ValueError`` from :class:`MarketObservationQuery`,
      never a silently empty result.

    Records are returned exactly as stored, provenance fields included.
    """

    def __init__(self, *, repository: MarketObservationRepository) -> None:
        self.repository = repository

    def get_market_observations(self, query: MarketObservationQuery) -> tuple[MarketObservation, ...]:
        return tuple(
            self.repository.list_for_city(
                query.city_code,
                geography_type=query.geography_type,
                geography_code=query.geography_code,
                metric_name=query.metric_name,
                start_date=query.start_date,
                end_date=query.end_date,
            )
        )
