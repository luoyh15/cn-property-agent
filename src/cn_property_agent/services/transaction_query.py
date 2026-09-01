from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cn_property_agent.domain import Transaction
from cn_property_agent.storage.repositories import TransactionRepository


class TransactionQuery(BaseModel):
    """One read request over canonical transactions.

    The date bounds are inclusive on both ends and independently optional; an
    omitted bound means "unbounded in that direction". Invalid input is
    rejected here, at construction time, so a service call can only fail for
    storage reasons.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    community_id: str = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("community_id")
    @classmethod
    def validate_community_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("community_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "TransactionQuery":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class TransactionQueryService:
    """Source-independent read access to stored canonical transactions.

    The service reads only what ingestion has already persisted; it never
    contacts a provider, so a community with no stored rows yields an empty
    result rather than a fetch. Semantics:

    - no matching rows: an empty tuple, which is a success, not an error;
    - invalid input: ``ValueError`` from :class:`TransactionQuery`, never a
      silently empty result;
    - ordering: newest ``deal_date`` first, ties broken by ``transaction_id``,
      so repeated identical queries return an identical sequence;
    - duplicates: ``transaction_id`` is the storage primary key, so a
      transaction appears at most once regardless of how often it was ingested.

    Records are returned exactly as stored, provenance fields included.
    """

    def __init__(self, *, repository: TransactionRepository) -> None:
        self.repository = repository

    def get_transactions(self, query: TransactionQuery) -> tuple[Transaction, ...]:
        return tuple(
            self.repository.list_for_community(
                query.community_id,
                start_date=query.start_date,
                end_date=query.end_date,
            )
        )
