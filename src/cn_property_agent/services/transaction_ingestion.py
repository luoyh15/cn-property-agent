from __future__ import annotations

import logging
import time
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cn_property_agent.domain import Community, FrozenModel, Transaction
from cn_property_agent.providers import ParseRejection, TransactionProvider
from cn_property_agent.storage.repositories import TransactionRepository

from .errors import ProviderFetchError
from .transaction_normalization import (
    DEFAULT_UNIT_PRICE_TOLERANCE,
    NormalizedTransaction,
    RejectionReason,
    TransactionRejection,
    normalize_transaction,
    validate_unit_price_tolerance,
)

logger = logging.getLogger(__name__)


class TransactionIngestionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    community: Community
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "TransactionIngestionRequest":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class TransactionIngestionResult(FrozenModel):
    """Explicit outcome of one ingestion operation.

    Two independent failure vocabularies are reported side by side and never
    merged into a single number:

    - ``parse_rejections``: source rows the provider's parser could not
      interpret at all, carried through from the fetch result;
    - ``quality_rejections``: parsed records that failed a canonical
      data-quality gate.

    The counts read as ``source_row_count >= parsed_count +
    parse_rejection_count`` and ``parsed_count == upserted_count +
    quality_rejection_count``. Derived counts are computed from the rejection
    tuples so they cannot drift from the reasons they summarize.
    """

    community_id: str = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    source_row_count: int = Field(ge=0)
    parsed_count: int = Field(ge=0)
    upserted_count: int = Field(ge=0)
    transaction_ids: tuple[str, ...] = ()
    parse_rejections: tuple[ParseRejection, ...] = ()
    quality_rejections: tuple[TransactionRejection, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def parse_rejection_count(self) -> int:
        return len(self.parse_rejections)

    @property
    def quality_rejection_count(self) -> int:
        return len(self.quality_rejections)

    @property
    def rejection_count(self) -> int:
        """Every row lost on the way to storage, parser and gates combined."""
        return self.parse_rejection_count + self.quality_rejection_count


class TransactionIngestionService:
    """Source-independent transaction ingestion.

    Fetches provider records for a resolved community, applies deterministic
    data-quality gates, and persists canonical transactions idempotently.
    """

    def __init__(
        self,
        *,
        provider: TransactionProvider,
        repository: TransactionRepository,
        unit_price_tolerance: float = DEFAULT_UNIT_PRICE_TOLERANCE,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.unit_price_tolerance = validate_unit_price_tolerance(unit_price_tolerance)

    async def ingest(self, request: TransactionIngestionRequest) -> TransactionIngestionResult:
        community = request.community
        started_at = time.perf_counter()

        try:
            fetched = await self.provider.fetch_transactions(
                community,
                start_date=request.start_date,
                end_date=request.end_date,
            )
        except Exception as error:
            raise ProviderFetchError(
                provider=type(self.provider).__name__,
                subject_id=community.community_id,
                message=str(error),
            ) from error

        accepted: dict[str, Transaction] = {}
        rejections: list[TransactionRejection] = []
        warnings: list[str] = []

        for record in fetched.records:
            outcome = normalize_transaction(
                record,
                community=community,
                start_date=request.start_date,
                end_date=request.end_date,
                unit_price_tolerance=self.unit_price_tolerance,
            )
            if isinstance(outcome, TransactionRejection):
                rejections.append(outcome)
                continue
            self._accept(outcome, accepted, rejections, warnings)

        upserted_count = self.repository.upsert_many(accepted.values())

        logger.info(
            "transaction ingestion community_id=%s source_rows=%d parsed=%d parse_rejected=%d"
            " upserted=%d quality_rejected=%d duration_s=%.3f",
            community.community_id,
            fetched.source_row_count,
            fetched.parsed_count,
            fetched.parse_rejection_count,
            upserted_count,
            len(rejections),
            time.perf_counter() - started_at,
        )

        return TransactionIngestionResult(
            community_id=community.community_id,
            start_date=request.start_date,
            end_date=request.end_date,
            source_row_count=fetched.source_row_count,
            parsed_count=fetched.parsed_count,
            upserted_count=upserted_count,
            transaction_ids=tuple(accepted),
            parse_rejections=fetched.parse_rejections,
            quality_rejections=tuple(rejections),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _accept(
        outcome: NormalizedTransaction,
        accepted: dict[str, Transaction],
        rejections: list[TransactionRejection],
        warnings: list[str],
    ) -> None:
        transaction = outcome.transaction
        if transaction.transaction_id in accepted:
            rejections.append(
                TransactionRejection(
                    source=transaction.source,
                    source_transaction_id=transaction.source_transaction_id,
                    source_url=transaction.source_url,
                    reason=RejectionReason.DUPLICATE_IN_BATCH,
                    detail=f"transaction_id {transaction.transaction_id} already present in this batch",
                )
            )
            return
        accepted[transaction.transaction_id] = transaction
        warnings.extend(f"{transaction.transaction_id}: {warning}" for warning in outcome.warnings)
