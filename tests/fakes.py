from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from cn_property_agent.domain import Community, ListingObservation, MarketObservation
from cn_property_agent.providers import (
    ListingFetchResult,
    MarketObservationFetchResult,
    RawTransactionRecord,
    TransactionFetchResult,
)


class FakeTransactionProvider:
    """In-memory `TransactionProvider` for tests only.

    Configured fetch results are returned verbatim, including out-of-range or
    malformed records, so that service-side quality gates are what the tests
    actually exercise. A bare record sequence is accepted as shorthand for "all
    of these rows parsed cleanly".
    """

    def __init__(
        self,
        results: Mapping[str, TransactionFetchResult | Sequence[RawTransactionRecord]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = {
            community_id: _as_fetch_result(value)
            for community_id, value in (results or {}).items()
        }
        self._error = error
        self.calls: list[tuple[str, date | None, date | None]] = []

    async def fetch_transactions(
        self,
        community: Community,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> TransactionFetchResult:
        self.calls.append((community.community_id, start_date, end_date))
        if self._error is not None:
            raise self._error
        return self._results.get(community.community_id, TransactionFetchResult())


def _as_fetch_result(
    value: TransactionFetchResult | Sequence[RawTransactionRecord],
) -> TransactionFetchResult:
    if isinstance(value, TransactionFetchResult):
        return value
    return TransactionFetchResult.from_records(value)


class FakeListingProvider:
    """In-memory `ListingProvider` for tests only.

    One instance answers with one configured snapshot per community, returned
    verbatim on every call, which is what makes replaying a snapshot testable.
    A later observation of the same market is modelled by :meth:`observe`,
    which swaps in the next snapshot the way a real source would have changed
    between two scheduled fetches.
    """

    def __init__(
        self,
        results: Mapping[str, ListingFetchResult | Sequence[ListingObservation]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = {
            community_id: _as_listing_fetch_result(value)
            for community_id, value in (results or {}).items()
        }
        self._error = error
        self.calls: list[str] = []

    def observe(
        self,
        community: Community,
        value: ListingFetchResult | Sequence[ListingObservation],
    ) -> None:
        """Replace what the source shows for `community` from now on."""
        self._results[community.community_id] = _as_listing_fetch_result(value)

    async def fetch_current_listings(self, community: Community) -> ListingFetchResult:
        self.calls.append(community.community_id)
        if self._error is not None:
            raise self._error
        return self._results.get(community.community_id, ListingFetchResult())


def _as_listing_fetch_result(
    value: ListingFetchResult | Sequence[ListingObservation],
) -> ListingFetchResult:
    if isinstance(value, ListingFetchResult):
        return value
    return ListingFetchResult.from_observations(value)


class FakeMarketObservationProvider:
    """In-memory `MarketObservationProvider` for tests only.

    One instance answers with one configured batch per requested `city_code`,
    returned verbatim on every call — including observations of another city,
    so that the service-side subject check is what the tests exercise. A city
    that was never configured publishes nothing, which is a successful empty
    batch rather than a failure. :meth:`publish` swaps in what the source shows
    from now on, the way a corrected figure would appear between two fetches.
    """

    def __init__(
        self,
        results: Mapping[str, MarketObservationFetchResult | Sequence[MarketObservation]]
        | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._results = {
            city_code: _as_market_observation_fetch_result(value)
            for city_code, value in (results or {}).items()
        }
        self._error = error
        self.calls: list[tuple[str, date | None, date | None, str | None]] = []

    def publish(
        self,
        city_code: str,
        value: MarketObservationFetchResult | Sequence[MarketObservation],
    ) -> None:
        """Replace what the source publishes for `city_code` from now on."""
        self._results[city_code] = _as_market_observation_fetch_result(value)

    async def fetch_market_observations(
        self,
        *,
        city_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        geography_code: str | None = None,
    ) -> MarketObservationFetchResult:
        self.calls.append((city_code, start_date, end_date, geography_code))
        if self._error is not None:
            raise self._error
        return self._results.get(city_code, MarketObservationFetchResult())


def _as_market_observation_fetch_result(
    value: MarketObservationFetchResult | Sequence[MarketObservation],
) -> MarketObservationFetchResult:
    if isinstance(value, MarketObservationFetchResult):
        return value
    return MarketObservationFetchResult.from_observations(value)
