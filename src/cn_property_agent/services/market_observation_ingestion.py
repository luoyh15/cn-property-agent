"""Source-independent ingestion of official market observations.

This is the write side of the benchmark series that
:class:`~cn_property_agent.services.market_observation_query.MarketObservationQueryService`
reads. Its subject is a geography rather than a community: an official series
exists independently of any community that will later be compared against it,
so the boundary of one ingestion request is a ``city_code``.

The service is deliberately thin. Observations arrive canonical from the
provider — a published figure carries its own identity, geography and period —
so there is nothing to normalize here, and provenance is written exactly as the
source supplied it.

Idempotence comes from storage rather than from bookkeeping in this module.
``observation_id`` is the primary key of ``market_observation``, so replaying an
unchanged batch rewrites the same rows, and a correction republished under the
same identifier overwrites every canonical field instead of creating a second
identity for the same measurement.

The whole batch is validated against the requested city before the first write.
``upsert`` is keyed by ``observation_id`` alone, so without that check a result
claiming to describe one city could move another city's stored observation, and
a stray record could leave its matching siblings behind as a half-applied batch.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cn_property_agent.domain import FrozenModel, MarketObservation
from cn_property_agent.providers import MarketObservationProvider
from cn_property_agent.storage.repositories import MarketObservationRepository

from .errors import ProviderContractError, ProviderFetchError

logger = logging.getLogger(__name__)


class MarketObservationIngestionRequest(BaseModel):
    """One acquisition request against one city's official series.

    ``city_code`` is the subject boundary and the only field the service
    enforces on the returned batch. The remaining fields narrow what is asked of
    the source and are forwarded to the provider verbatim; they are not
    post-fetch filters, so a provider that publishes a wider window than it was
    asked for has its extra observations persisted rather than silently dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    city_code: str = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    geography_code: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "MarketObservationIngestionRequest":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class MarketObservationIngestionResult(FrozenModel):
    """Explicit outcome of one market-observation ingestion.

    Each count has exactly one meaning:

    - ``source_observation_count``: observations the provider returned;
    - ``persisted_observation_count``: observations written to storage.

    Because this service applies no quality gate of its own, the two are equal;
    they are reported separately so a later gate cannot change the meaning of an
    existing number.

    ``observation_ids`` names the distinct measurements the batch touched, in
    the order the provider reported them. It is shorter than
    ``persisted_observation_count`` when one batch carried the same
    ``observation_id`` twice: both are written, and the storage key collapses
    them onto one row.
    """

    city_code: str = Field(min_length=1)
    source_observation_count: int = Field(ge=0)
    persisted_observation_count: int = Field(ge=0)
    observation_ids: tuple[str, ...] = ()

    @property
    def observation_count(self) -> int:
        """Distinct measurements touched by this batch."""
        return len(self.observation_ids)


class MarketObservationIngestionService:
    """Fetch one city's official observations and persist them canonically.

    The provider is called exactly once per request, and persistence is
    sequential in the provider's own order, so a replay writes the same rows in
    the same sequence. The three outcomes stay distinguishable: a source that
    published nothing yields a successful empty result, a provider that could
    not deliver raises :class:`ProviderFetchError`, and one that answered about
    another city raises :class:`ProviderContractError`.
    """

    def __init__(
        self,
        *,
        provider: MarketObservationProvider,
        repository: MarketObservationRepository,
    ) -> None:
        self.provider = provider
        self.repository = repository

    async def ingest(
        self, request: MarketObservationIngestionRequest
    ) -> MarketObservationIngestionResult:
        started_at = time.perf_counter()

        try:
            fetched = await self.provider.fetch_market_observations(
                city_code=request.city_code,
                start_date=request.start_date,
                end_date=request.end_date,
                geography_code=request.geography_code,
            )
        except Exception as error:
            raise ProviderFetchError(
                provider=type(self.provider).__name__,
                subject_id=request.city_code,
                message=str(error),
            ) from error

        self._require_requested_city(request.city_code, fetched.observations)

        observation_ids: list[str] = []
        persisted_count = 0
        for observation in fetched.observations:
            self.repository.upsert(observation)
            persisted_count += 1
            if observation.observation_id not in observation_ids:
                observation_ids.append(observation.observation_id)

        logger.info(
            "market observation ingestion city_code=%s source_observations=%d persisted=%d"
            " observations=%d duration_s=%.3f",
            request.city_code,
            fetched.observation_count,
            persisted_count,
            len(observation_ids),
            time.perf_counter() - started_at,
        )

        return MarketObservationIngestionResult(
            city_code=request.city_code,
            source_observation_count=fetched.observation_count,
            persisted_observation_count=persisted_count,
            observation_ids=tuple(observation_ids),
        )

    def _require_requested_city(
        self,
        city_code: str,
        observations: Iterable[MarketObservation],
    ) -> None:
        """Reject the whole batch unless every observation belongs to `city_code`.

        Checked up front, over the complete batch, so that a foreign-city
        observation is neither persisted itself nor able to leave the matching
        records ahead of it written as a half-applied batch.
        """
        for observation in observations:
            if observation.city_code != city_code:
                raise ProviderContractError(
                    provider=type(self.provider).__name__,
                    subject_id=city_code,
                    message=(
                        f"observation {observation.observation_id} belongs to city "
                        f"{observation.city_code}"
                    ),
                )
