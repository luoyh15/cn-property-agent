"""Source-independent read access to canonical listings and their history.

Ingestion keeps listing identity and listing history in two tables on purpose,
and this module is the read side of that split:

- *current listings* answer "what is on the market in this community", which is
  every stored identity of the community paired with its newest stored snapshot;
- *history* answers "how did this one listing move", which is every snapshot
  ever stored for it, oldest first.

Nothing here contacts a provider. "Current" therefore means *latest persisted*,
as of the last ingestion run, not "live at the source"; a community that was
never ingested reads as empty rather than triggering a fetch.
"""

from __future__ import annotations

from pydantic import model_validator

from cn_property_agent.domain import FrozenModel, Listing, ListingSnapshot
from cn_property_agent.storage.repositories import ListingRepository


class CurrentListing(FrozenModel):
    """One stable listing identity plus the newest snapshot stored for it.

    ``latest_snapshot`` is optional because a snapshot is never invented: a
    listing whose identity exists without any stored snapshot is reported as
    itself with ``None``, instead of being hidden from the community view or
    given a fabricated price. Both parts are returned exactly as stored, so
    the snapshot keeps its provenance (``source``, ``source_url``,
    ``raw_payload_ref``, ``parser_version``, ``snapshot_at``) and the listing
    keeps its canonical identity, seen range and status.
    """

    listing: Listing
    latest_snapshot: ListingSnapshot | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "CurrentListing":
        if self.latest_snapshot is not None and self.latest_snapshot.listing_id != self.listing.listing_id:
            raise ValueError("listing and latest_snapshot listing_id must match")
        return self

    @property
    def listing_id(self) -> str:
        return self.listing.listing_id


class ListingQueryService:
    """Read canonical current listings and snapshot history from storage.

    Semantics:

    - current listings are restricted to the requested ``community_id``;
      listings of any other community are excluded, including when they share a
      source;
    - each listing appears at most once, carrying its latest snapshot, so a
      later snapshot replaces what the current view reports for that listing
      while the earlier snapshots stay readable through
      :meth:`get_listing_history`;
    - current listings are ordered by ``last_seen_at`` descending and then by
      ``listing_id``, which is a total order because ``listing_id`` is unique;
    - history is chronological by ``snapshot_at``, oldest first;
    - an unknown or empty community, and an unknown listing, are empty
      successes rather than errors, matching the transaction query service.
    """

    def __init__(self, *, repository: ListingRepository) -> None:
        self.repository = repository

    def get_listings(self, community_id: str) -> tuple[Listing, ...]:
        """Canonical listing identities of one community, without snapshots.

        The identity half of :meth:`get_current_listings`, for callers that
        need the listings themselves rather than what each is asking now.
        """
        return tuple(self.repository.list_for_community(community_id))

    def get_current_listings(self, community_id: str) -> tuple[CurrentListing, ...]:
        latest = self.repository.latest_snapshots_for_community(community_id)
        return tuple(
            CurrentListing(listing=listing, latest_snapshot=latest.get(listing.listing_id))
            for listing in self.repository.list_for_community(community_id)
        )

    def get_listing_history(self, listing_id: str) -> tuple[ListingSnapshot, ...]:
        return tuple(self.repository.history(listing_id))

    def get_community_listing_history(self, community_id: str) -> tuple[ListingSnapshot, ...]:
        """Every stored snapshot of every listing of one community.

        The whole community's history in one read, so a caller that needs the
        complete series — repricing describes a listing's first and latest
        asking price, which the current view alone cannot answer — does not
        walk :meth:`get_listing_history` once per listing. Snapshots arrive
        grouped by ``listing_id`` and chronological within each listing.
        """
        return tuple(self.repository.history_for_community(community_id))
