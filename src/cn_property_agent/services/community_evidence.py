"""One read answering "what market evidence do we currently hold about X?".

The transaction and listing paths are stored, queried and summarized
separately, and stay separate below this module. This is the single place that
assembles both for one community: it gathers the canonical transactions, the
canonical listing identities and the *complete* stored snapshot history of
those identities, then hands each set to the analytics function that already
defines what it means. No metric is recomputed, redefined or blended here.

Nothing here contacts a provider, reads a clock or chooses a window. "Currently
stored" means exactly the persisted evidence, so a community that was never
ingested reads as empty component metrics rather than triggering a fetch, and
the same storage state always yields the same result.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from cn_property_agent.analytics import (
    CommunityListingMetrics,
    CommunityTransactionMetrics,
    compute_community_listing_metrics,
)
from cn_property_agent.domain import FrozenModel
from cn_property_agent.services.listing_query import ListingQueryService
from cn_property_agent.services.transaction_query import TransactionQuery, TransactionQueryService


class CommunityMarketEvidence(FrozenModel):
    """The stored transaction and listing evidence for one community.

    The two component metrics are carried unchanged, not merged: each already
    states its own sample counts, thin-evidence semantics and the record ids it
    summarizes, and restating any of that here would create a second definition
    of the same fact. This model therefore adds only the shared subject and
    convenience access to the recency each component already reports.

    Recency comes from the evidence itself — the newest stored deal date and
    the newest stored snapshot instant — never from the current time. Either is
    ``None`` when that half of the evidence is empty, which is the honest answer
    to "how fresh is it" when there is nothing to be fresh.
    """

    community_id: str = Field(min_length=1)
    transaction_metrics: CommunityTransactionMetrics
    listing_metrics: CommunityListingMetrics

    @model_validator(mode="after")
    def validate_subject(self) -> "CommunityMarketEvidence":
        foreign = sorted(
            {self.transaction_metrics.community_id, self.listing_metrics.community_id}
            - {self.community_id}
        )
        if foreign:
            raise ValueError(
                f"component metrics must describe community {self.community_id!r},"
                f" also got: {', '.join(repr(item) for item in foreign)}"
            )
        return self

    @property
    def latest_deal_date(self) -> date | None:
        """Newest stored deal date, ``None`` when no transaction is stored."""
        return self.transaction_metrics.latest_deal_date

    @property
    def latest_snapshot_at(self) -> datetime | None:
        """Newest stored snapshot instant, ``None`` when nothing was observed."""
        return self.listing_metrics.latest_snapshot_at

    @property
    def has_evidence(self) -> bool:
        """Whether either half holds anything at all."""
        return self.transaction_metrics.has_transactions or self.listing_metrics.has_listings


class CommunityEvidenceService:
    """Assemble one community's stored market evidence from the read paths.

    Composition only: the two query services keep supplying the canonical
    records with their provenance, and the existing analytics functions keep
    deciding what those records mean. Each component metric is exactly what a
    caller would get by querying and computing it directly.

    Evidence cannot be mixed across communities: both query paths filter on the
    requested ``community_id``, and both analytics functions reject a record
    belonging to another community rather than silently dropping it. A blank
    ``community_id`` is rejected — metrics always name their subject — while an
    unknown one is an empty success.
    """

    def __init__(
        self,
        *,
        transactions: TransactionQueryService,
        listings: ListingQueryService,
    ) -> None:
        self.transactions = transactions
        self.listings = listings

    def get_market_evidence(self, community_id: str) -> CommunityMarketEvidence:
        query = TransactionQuery(community_id=community_id)
        return CommunityMarketEvidence(
            community_id=query.community_id,
            transaction_metrics=self.transactions.get_transaction_metrics(query),
            listing_metrics=compute_community_listing_metrics(
                self.listings.get_listings(query.community_id),
                self.listings.get_community_listing_history(query.community_id),
                community_id=query.community_id,
            ),
        )
