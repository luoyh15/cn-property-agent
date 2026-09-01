from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from pydantic import Field

from cn_property_agent.domain import FrozenModel

from .dto import RawTransactionRecord


class ParseRejectionReason(StrEnum):
    """Why one source row could not be represented as a provider DTO.

    These are *parser* failures (the source value could not be interpreted at
    all). Business plausibility — positive prices, dates in range, total/unit
    price consistency — stays with the canonical data-quality gates in the
    service layer, so the two failure vocabularies never overlap.
    """

    MISSING_SOURCE_IDENTITY = "missing_source_identity"
    MALFORMED_FIELD = "malformed_field"
    MALFORMED_ROW = "malformed_row"
    SCHEMA_INVALID = "schema_invalid"


class SourceRowRef(FrozenModel):
    """Minimal pointer back to one source row.

    Deliberately excludes the raw payload: a rejection is meant to be logged,
    counted and stored cheaply. The payload itself stays behind
    ``raw_payload_ref`` under the provider's own snapshot policy.
    """

    source: str = Field(min_length=1)
    row_index: int | None = Field(default=None, ge=0)
    source_row_id: str | None = None
    source_url: str | None = None
    raw_payload_ref: str | None = None


class ParseRejection(FrozenModel):
    """One source row that a parser refused to interpret."""

    row: SourceRowRef
    reason: ParseRejectionReason
    field: str | None = None
    detail: str = Field(min_length=1)


class ParseResult(FrozenModel):
    """Outcome of parsing a batch of source rows.

    Parsing is never all-or-nothing: a row the parser cannot interpret becomes
    a :class:`ParseRejection` while its siblings still yield records.
    """

    records: tuple[RawTransactionRecord, ...] = ()
    rejections: tuple[ParseRejection, ...] = ()

    @property
    def parsed_count(self) -> int:
        return len(self.records)

    @property
    def rejected_count(self) -> int:
        return len(self.rejections)


class FieldParseError(ValueError):
    """Raised by a parser helper when a single source value is unintelligible.

    Carries the reason/field so the calling parser can turn it into a
    :class:`ParseRejection` without inventing its own error taxonomy.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        reason: ParseRejectionReason = ParseRejectionReason.MALFORMED_FIELD,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.reason = reason


def build_parse_result(
    outcomes: Iterable[RawTransactionRecord | ParseRejection],
) -> ParseResult:
    """Split per-row outcomes into a :class:`ParseResult`, preserving order."""
    records: list[RawTransactionRecord] = []
    rejections: list[ParseRejection] = []
    for outcome in outcomes:
        if isinstance(outcome, ParseRejection):
            rejections.append(outcome)
        else:
            records.append(outcome)
    return ParseResult(records=tuple(records), rejections=tuple(rejections))
