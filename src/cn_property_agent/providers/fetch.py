"""What a provider hands back to the ingestion service.

Acquisition and interpretation are separate steps, so a provider call has two
kinds of outcome: source rows it could interpret, and source rows the parser
refused. Both travel together in one fetch result so a malformed row stays
visible to callers instead of disappearing between the parser and the service.

Transport failures are not part of this envelope: they propagate as exceptions
and surface as ``ProviderFetchError``, so an empty successful fetch can never be
confused with a broken one.

Sources whose adapters do not interpret rows yet carry only the parsed side of
that split; see :class:`MarketObservationFetchResult`.
"""

from __future__ import annotations

from typing import Any, ClassVar, Iterable, Mapping

from pydantic import Field, model_validator

from cn_property_agent.domain import FrozenModel, ListingObservation, MarketObservation

from .dto import RawTransactionRecord
from .parsing import ListingParseResult, ParseRejection, ParseResult


class _FetchResult(FrozenModel):
    """Row accounting shared by every provider fetch result.

    Each count has exactly one meaning:

    - ``source_row_count``: source rows the provider observed for this request;
    - ``parsed_count``: rows the parser could interpret;
    - ``parse_rejections``/``parse_rejection_count``: rows it could not.

    ``source_row_count`` defaults to ``parsed_count + parse_rejection_count``
    and may only be larger, for a provider that discards rows before parsing
    (paging artefacts, rows of another kind). It is never smaller.

    Parse rejections stay in the parser vocabulary: they are not, and must not
    be converted into, the canonical rejection types of the service layer.
    """

    parse_rejections: tuple[ParseRejection, ...] = ()
    source_row_count: int = Field(default=0, ge=0)

    #: Name of the subclass field holding the successfully parsed rows.
    _parsed_field: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def _default_source_row_count(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and data.get("source_row_count") is None:
            accounted = len(data.get(cls._parsed_field) or ()) + len(
                data.get("parse_rejections") or ()
            )
            return {**data, "source_row_count": accounted}
        return data

    @model_validator(mode="after")
    def _validate_source_row_count(self) -> "_FetchResult":
        accounted = self.parsed_count + self.parse_rejection_count
        if self.source_row_count < accounted:
            raise ValueError(
                f"source_row_count {self.source_row_count} is smaller than the"
                f" {accounted} rows already accounted for"
            )
        return self

    @property
    def parsed_count(self) -> int:
        return len(getattr(self, type(self)._parsed_field))

    @property
    def parse_rejection_count(self) -> int:
        return len(self.parse_rejections)


class TransactionFetchResult(_FetchResult):
    """Outcome of one ``fetch_transactions`` call.

    ``records`` holds the provider DTOs the parser produced; normalization into
    canonical transactions stays with the ingestion service.
    """

    records: tuple[RawTransactionRecord, ...] = ()

    _parsed_field: ClassVar[str] = "records"

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


class ListingFetchResult(_FetchResult):
    """Outcome of one ``fetch_current_listings`` call.

    The listing sibling of :class:`TransactionFetchResult`, with the same count
    semantics. It carries canonical
    :class:`~cn_property_agent.domain.ListingObservation` values rather than a
    provider DTO for the reason the listing parser already emits them: one
    observation is what a single snapshot saw, so nothing needs reconciling
    before it is meaningful.

    A snapshot that genuinely listed nothing is a successful empty result; a
    snapshot the provider could not read at all raises instead.
    """

    observations: tuple[ListingObservation, ...] = ()

    _parsed_field: ClassVar[str] = "observations"

    @classmethod
    def from_parse_result(
        cls,
        parse_result: ListingParseResult,
        *,
        source_row_count: int | None = None,
    ) -> "ListingFetchResult":
        """Wrap a parser batch, keeping its rejections attached to its observations."""
        return cls(
            observations=parse_result.observations,
            parse_rejections=parse_result.rejections,
            source_row_count=source_row_count,
        )

    @classmethod
    def from_observations(
        cls,
        observations: Iterable[ListingObservation],
        *,
        source_row_count: int | None = None,
    ) -> "ListingFetchResult":
        """Every observed row parsed cleanly — the common case for imports/fakes."""
        return cls(observations=tuple(observations), source_row_count=source_row_count)


class MarketObservationFetchResult(FrozenModel):
    """Outcome of one ``fetch_market_observations`` call.

    The envelope is deliberately smaller than its transaction and listing
    siblings: it distinguishes the two outcomes that already exist for official
    market data, and nothing more.

    - A batch of canonical
      :class:`~cn_property_agent.domain.MarketObservation` records, possibly
      empty. An empty batch is a genuine answer — the source published nothing
      for the requested subject and window — and is a success.
    - A provider, input or transport failure, which raises instead of
      returning. An acquisition that did not happen can therefore never reach
      storage disguised as a market with no published figures.

    There is no per-row rejection channel yet, because there is no market
    parser yet: the row accounting of :class:`_FetchResult` only means
    something once an adapter interprets published rows. Adding one later is an
    additive change to this shape, not a change to the protocol.

    Observations are canonical on arrival. A provider that cannot produce a
    canonical record must fail rather than emit a provider-specific
    market-observation DTO for the service layer to reinterpret.
    """

    observations: tuple[MarketObservation, ...] = ()

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @classmethod
    def from_observations(
        cls,
        observations: Iterable[MarketObservation],
    ) -> "MarketObservationFetchResult":
        """Wrap an already canonical batch — the common case for imports/fakes."""
        return cls(observations=tuple(observations))
