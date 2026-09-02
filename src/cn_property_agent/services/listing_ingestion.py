"""Source-independent current-listing ingestion.

One provider fetch is one observation of the market. Persisting it means two
different things at once, and this service keeps them apart:

- the **listing identity** is upserted, so the same source listing keeps one
  canonical row no matter how often it is observed;
- the **snapshot** is appended to the listing's history, so what was asked at
  that instant is never lost by overwriting what was asked before.

Nothing here knows which source the listings came from: the service depends on
the ``ListingProvider`` protocol, canonical domain models and the storage
repository only.

Idempotence comes from the storage keys rather than from bookkeeping in this
module. ``listing_id`` keys the identity row and ``(listing_id, snapshot_at)``
keys the history row, so replaying an unchanged snapshot rewrites the same two
rows instead of duplicating them, while a later snapshot of the same listing
adds exactly one new history point and advances ``last_seen_at``.

Community identity is not re-derived here. The observation carries the
``community_id`` the caller already resolved before the fetch; this service
neither guesses nor rewrites it, exactly as it never rewrites provenance.
"""

from __future__ import annotations

import logging
import time

from pydantic import Field

from cn_property_agent.domain import Community, FrozenModel
from cn_property_agent.providers import ListingProvider, ParseRejection
from cn_property_agent.storage.repositories import ListingRepository

from .errors import ProviderFetchError

logger = logging.getLogger(__name__)


class ListingIngestionResult(FrozenModel):
    """Explicit outcome of one snapshot ingestion.

    Each count has exactly one meaning:

    - ``source_row_count``: source rows the provider observed;
    - ``parsed_count``: rows its parser could interpret;
    - ``parse_rejections``/``parse_rejection_count``: rows it could not;
    - ``persisted_observation_count``: observations written to storage.

    The counts read as ``source_row_count >= parsed_count +
    parse_rejection_count`` and, because this service applies no quality gate of
    its own, ``persisted_observation_count == parsed_count``.

    ``listing_ids`` names the distinct listing identities the snapshot touched,
    in the order the provider reported them. It is shorter than
    ``persisted_observation_count`` when one batch observed the same listing
    twice: both observations are written, and the storage keys collapse them.

    Parse rejections stay in the parser vocabulary. They are carried through
    verbatim and never translated into a service-level rejection type.
    """

    community_id: str = Field(min_length=1)
    source_row_count: int = Field(ge=0)
    parsed_count: int = Field(ge=0)
    persisted_observation_count: int = Field(ge=0)
    listing_ids: tuple[str, ...] = ()
    parse_rejections: tuple[ParseRejection, ...] = ()

    @property
    def parse_rejection_count(self) -> int:
        return len(self.parse_rejections)

    @property
    def listing_count(self) -> int:
        """Distinct listing identities touched by this snapshot."""
        return len(self.listing_ids)


class ListingIngestionService:
    """Fetch one current-listing snapshot and persist it as identity + history.

    Persistence is sequential and follows the provider's own row order, so a
    replay writes the same rows in the same sequence. A source that genuinely
    listed nothing yields a successful empty result; a provider that could not
    deliver at all raises :class:`ProviderFetchError`, so the two can never be
    confused.
    """

    def __init__(
        self,
        *,
        provider: ListingProvider,
        repository: ListingRepository,
    ) -> None:
        self.provider = provider
        self.repository = repository

    async def ingest(self, community: Community) -> ListingIngestionResult:
        started_at = time.perf_counter()

        try:
            fetched = await self.provider.fetch_current_listings(community)
        except Exception as error:
            raise ProviderFetchError(
                provider=type(self.provider).__name__,
                subject_id=community.community_id,
                message=str(error),
            ) from error

        listing_ids: list[str] = []
        persisted_count = 0
        for observation in fetched.observations:
            # Identity first: the history row references a listing that exists.
            self.repository.upsert_listing(observation.listing)
            self.repository.append_snapshot(observation.snapshot)
            persisted_count += 1
            if observation.listing.listing_id not in listing_ids:
                listing_ids.append(observation.listing.listing_id)

        logger.info(
            "listing ingestion community_id=%s source_rows=%d parsed=%d parse_rejected=%d"
            " persisted=%d listings=%d duration_s=%.3f",
            community.community_id,
            fetched.source_row_count,
            fetched.parsed_count,
            fetched.parse_rejection_count,
            persisted_count,
            len(listing_ids),
            time.perf_counter() - started_at,
        )

        return ListingIngestionResult(
            community_id=community.community_id,
            source_row_count=fetched.source_row_count,
            parsed_count=fetched.parsed_count,
            persisted_observation_count=persisted_count,
            listing_ids=tuple(listing_ids),
            parse_rejections=fetched.parse_rejections,
        )
