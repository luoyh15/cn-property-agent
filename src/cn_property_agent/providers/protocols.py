from __future__ import annotations

from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from cn_property_agent.domain import (
    CommuteMetric,
    Community,
    GeocodeResult,
    LandParcel,
    ListingObservation,
    MarketObservation,
    POI,
    TransportMode,
)

from .fetch import TransactionFetchResult


@runtime_checkable
class TransactionProvider(Protocol):
    """Fetch observed transactions for an already resolved community.

    Providers return source-independent DTOs; the ingestion service owns
    normalization into canonical :class:`~cn_property_agent.domain.Transaction`
    records because internal identity is not knowable by an adapter.

    The return value is a :class:`TransactionFetchResult` rather than a bare
    sequence so that rows the parser could not interpret reach the caller
    instead of being dropped inside the adapter. Transport failures raise.
    """

    async def fetch_transactions(
        self,
        community: Community,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> TransactionFetchResult: ...


@runtime_checkable
class ListingProvider(Protocol):
    async def fetch_current_listings(
        self,
        community: Community,
    ) -> Sequence[ListingObservation]: ...


@runtime_checkable
class GeoProvider(Protocol):
    async def geocode(
        self,
        query: str,
        *,
        city_code: str,
    ) -> Sequence[GeocodeResult]: ...

    async def nearby_poi(
        self,
        *,
        city_code: str,
        latitude: float,
        longitude: float,
        radius_m: int,
        categories: Sequence[str] = (),
    ) -> Sequence[POI]: ...

    async def commute(
        self,
        *,
        origin: Community,
        destination: str,
        mode: TransportMode,
    ) -> CommuteMetric: ...


@runtime_checkable
class MarketProvider(Protocol):
    async def fetch_market_observations(
        self,
        *,
        city_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        geography_code: str | None = None,
    ) -> Sequence[MarketObservation]: ...


@runtime_checkable
class PlanningProvider(Protocol):
    async def fetch_land_supply(
        self,
        *,
        city_code: str,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> Sequence[LandParcel]: ...
