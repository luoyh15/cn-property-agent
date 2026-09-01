"""What a transaction provider hands back to the ingestion service.

Acquisition and interpretation are separate steps, so a provider call has two
kinds of outcome: source rows it could turn into DTOs, and source rows the
parser refused. Both travel together in :class:`TransactionFetchResult` so a
malformed row stays visible to callers instead of disappearing between the
parser and the service.

Transport failures are not part of this envelope: they propagate as exceptions
and surface as ``ProviderFetchError``, so an empty successful fetch can never be
confused with a broken one.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import Field, model_validator

from cn_property_agent.domain import FrozenModel

from .dto import RawTransactionRecord
from .parsing import ParseRejection, ParseResult


class TransactionFetchResult(FrozenModel):
    """Outcome of one ``fetch_transactions`` call.

    Each count has exactly one meaning:

    - ``source_row_count``: source rows the provider observed for this request;
    - ``records``/``parsed_count``: rows the parser could interpret;
    - ``parse_rejections``/``parse_rejection_count``: rows it could not.

    ``source_row_count`` defaults to ``parsed_count + parse_rejection_count``
    and may only be larger, for a provider that discards rows before parsing
    (paging artefacts, non-transaction rows). It is never smaller.

    Parse rejections stay in the parser vocabulary: they are not, and must not
    be converted into, canonical
    :class:`~cn_property_agent.services.TransactionRejection` values.
    """

    records: tuple[RawTransactionRecord, ...] = ()
    parse_rejections: tuple[ParseRejection, ...] = ()
    source_row_count: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _default_source_row_count(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and data.get("source_row_count") is None:
            accounted = len(data.get("records") or ()) + len(data.get("parse_rejections") or ())
            return {**data, "source_row_count": accounted}
        return data

    @model_validator(mode="after")
    def _validate_source_row_count(self) -> "TransactionFetchResult":
        accounted = self.parsed_count + self.parse_rejection_count
        if self.source_row_count < accounted:
            raise ValueError(
                f"source_row_count {self.source_row_count} is smaller than the"
                f" {accounted} rows already accounted for"
            )
        return self

    @property
    def parsed_count(self) -> int:
        return len(self.records)

    @property
    def parse_rejection_count(self) -> int:
        return len(self.parse_rejections)

    @classmethod
    def from_parse_result(
        cls,
        parse_result: ParseResult,
        *,
        source_row_count: int | None = None,
    ) -> "TransactionFetchResult":
        """Wrap a parser batch, keeping its rejections attached to its records."""
        return cls(
            records=parse_result.records,
            parse_rejections=parse_result.rejections,
            source_row_count=source_row_count,
        )

    @classmethod
    def from_records(
        cls,
        records: Iterable[RawTransactionRecord],
        *,
        source_row_count: int | None = None,
    ) -> "TransactionFetchResult":
        """Every observed row parsed cleanly — the common case for imports/fakes."""
        return cls(records=tuple(records), source_row_count=source_row_count)
