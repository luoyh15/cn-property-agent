"""Recorded Lianjia current-listing provider backed by a local JSON snapshot.

This adapter replays listing rows that were captured earlier and written to a
file. It performs no network, browser or clock access at all: the batch identity
and provenance (``community_id``, ``snapshot_at``, ``source_url``,
``raw_payload_ref``) are part of the snapshot, and field interpretation is
delegated in full to
:func:`~cn_property_agent.providers.lianjia.parse_listing_rows`.

Snapshot format::

    {
      "community_id": "cm-sh-pd-002",
      "snapshot_at": "2026-08-01T00:00:00Z",
      "source_url": "https://.../ershoufang/...",       # optional
      "raw_payload_ref": "snapshot://.../page-1",       # optional
      "rows": [ {"房源编号": "...", "总价": "...", ...}, ... ]
    }

``rows`` holds already-extracted Lianjia field mappings, exactly what the parser
consumes; a row may override the batch ``source_url``/``raw_payload_ref`` — and
``snapshot_at`` — with its own.

Community identity is recorded, not inferred: the snapshot names the community
it was captured for, and a request for a different community is refused rather
than answered with another community's listings.

Failure boundary: a row the parser cannot interpret becomes a
:class:`~cn_property_agent.providers.ParseRejection` alongside its valid
siblings, while a snapshot that cannot be read as a batch at all raises
:class:`~cn_property_agent.providers.lianjia.LianjiaSnapshotError`. An unusable
input therefore never reaches the caller disguised as an empty market.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from cn_property_agent.domain import Community
from cn_property_agent.providers import ListingFetchResult

from .listing_parser import LianjiaListingParseContext, parse_listing_rows
from .recorded import LianjiaSnapshotError, format_validation_error, load_snapshot_document


class LianjiaListingSnapshot(BaseModel):
    """One recorded batch: which community was observed, when, and the rows seen.

    Unknown top-level keys are ignored so a snapshot may carry the capture
    tooling's own notes; the keys this adapter does read are validated strictly,
    so absent or malformed batch metadata still fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    community_id: str = Field(min_length=1)
    snapshot_at: AwareDatetime
    source_url: str | None = Field(default=None, min_length=1)
    raw_payload_ref: str | None = Field(default=None, min_length=1)
    rows: tuple[Any, ...]

    def parse_context(self) -> LianjiaListingParseContext:
        """Batch-level identity and provenance the rows themselves cannot supply."""
        return LianjiaListingParseContext(
            community_id=self.community_id,
            snapshot_at=self.snapshot_at,
            source_url=self.source_url,
            raw_payload_ref=self.raw_payload_ref,
        )


def load_listing_snapshot(path: Path | str) -> LianjiaListingSnapshot:
    """Read and validate one snapshot file, or raise :class:`LianjiaSnapshotError`."""
    snapshot_path = Path(path)
    payload = load_snapshot_document(snapshot_path)
    try:
        return LianjiaListingSnapshot.model_validate(payload)
    except ValidationError as error:
        raise LianjiaSnapshotError(snapshot_path, format_validation_error(error)) from error


class RecordedLianjiaListingProvider:
    """``ListingProvider`` replaying an explicitly supplied snapshot file.

    The file is re-read on every call, so the provider holds no cached state and
    repeated fetches of an unchanged snapshot are identical. Because the
    snapshot fixes ``snapshot_at``, replaying it is deterministic: the same file
    always yields the same observations.
    """

    def __init__(self, snapshot_path: Path | str) -> None:
        self.snapshot_path = Path(snapshot_path)

    async def fetch_current_listings(self, community: Community) -> ListingFetchResult:
        snapshot = load_listing_snapshot(self.snapshot_path)
        if snapshot.community_id != community.community_id:
            raise LianjiaSnapshotError(
                self.snapshot_path,
                f"was recorded for community {snapshot.community_id!r},"
                f" not the requested {community.community_id!r}",
            )
        parsed = parse_listing_rows(snapshot.rows, context=snapshot.parse_context())
        # Every recorded row is offered to the parser, so the observed count is
        # simply the length of the snapshot batch.
        return ListingFetchResult.from_parse_result(parsed, source_row_count=len(snapshot.rows))
