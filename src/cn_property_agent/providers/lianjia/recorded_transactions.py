"""Recorded Lianjia transaction provider backed by a local JSON snapshot.

This adapter replays transaction rows that were captured earlier and written to
a file. It performs no network, browser or clock access at all: the batch
provenance (``collected_at``, ``source_url``, ``raw_payload_ref``) is part of
the snapshot, and field interpretation is delegated in full to
:func:`~cn_property_agent.providers.lianjia.parse_transaction_rows`.

Snapshot format::

    {
      "collected_at": "2026-08-01T00:00:00Z",
      "source_url": "https://.../chengjiao/...",       # optional
      "raw_payload_ref": "snapshot://.../page-1",      # optional
      "rows": [ {"链家编号": "...", "成交日期": "...", ...}, ... ]
    }

``rows`` holds already-extracted Lianjia field mappings, exactly what the
parser consumes; a row may override the batch ``source_url``/``raw_payload_ref``
with its own.

Failure boundary: a row the parser cannot interpret becomes a
:class:`~cn_property_agent.providers.ParseRejection` alongside its valid
siblings, while a snapshot that cannot be read as a batch at all raises
:class:`LianjiaSnapshotError`. An unusable input therefore never reaches the
caller disguised as a successful empty fetch.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from cn_property_agent.domain import Community
from cn_property_agent.providers import TransactionFetchResult

from .recorded import LianjiaSnapshotError, format_validation_error, load_snapshot_document
from .transaction_parser import LianjiaParseContext, parse_transaction_rows


class LianjiaTransactionSnapshot(BaseModel):
    """One recorded batch: its provenance plus the rows observed with it.

    Unknown top-level keys are ignored so a snapshot may carry the capture
    tooling's own notes; the keys this adapter does read are validated
    strictly, so absent or malformed provenance still fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    collected_at: AwareDatetime
    source_url: str | None = Field(default=None, min_length=1)
    raw_payload_ref: str | None = Field(default=None, min_length=1)
    rows: tuple[Any, ...]

    def parse_context(self) -> LianjiaParseContext:
        """Batch-level provenance the rows themselves cannot supply."""
        return LianjiaParseContext(
            collected_at=self.collected_at,
            source_url=self.source_url,
            raw_payload_ref=self.raw_payload_ref,
        )


def load_transaction_snapshot(path: Path | str) -> LianjiaTransactionSnapshot:
    """Read and validate one snapshot file, or raise :class:`LianjiaSnapshotError`."""
    snapshot_path = Path(path)
    payload = load_snapshot_document(snapshot_path)
    try:
        return LianjiaTransactionSnapshot.model_validate(payload)
    except ValidationError as error:
        raise LianjiaSnapshotError(snapshot_path, format_validation_error(error)) from error


class RecordedLianjiaTransactionProvider:
    """``TransactionProvider`` replaying an explicitly supplied snapshot file.

    The snapshot is assumed to have been captured for the community it is
    handed to: ``community`` selects nothing here, and ``start_date``/
    ``end_date`` are accepted for protocol compatibility but do not filter.
    Callers that need windowing should filter downstream, where the canonical
    date semantics live.

    The file is re-read on every call, so the provider holds no cached state
    and repeated fetches of an unchanged snapshot are identical.
    """

    def __init__(self, snapshot_path: Path | str) -> None:
        self.snapshot_path = Path(snapshot_path)

    async def fetch_transactions(
        self,
        community: Community,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> TransactionFetchResult:
        snapshot = load_transaction_snapshot(self.snapshot_path)
        parsed = parse_transaction_rows(snapshot.rows, context=snapshot.parse_context())
        # Every recorded row is offered to the parser, so the observed count is
        # simply the length of the snapshot batch.
        return TransactionFetchResult.from_parse_result(
            parsed,
            source_row_count=len(snapshot.rows),
        )
