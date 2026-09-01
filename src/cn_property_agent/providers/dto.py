from __future__ import annotations

from datetime import date

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RawTransactionRecord(BaseModel):
    """Source-independent provider output for one observed transaction.

    Providers map their own payloads onto this DTO. It intentionally does not
    carry internal identifiers (``transaction_id``/``community_id``): those are
    assigned by the ingestion service against a resolved community.

    Measurement fields are deliberately permissive so that data-quality gates
    stay in one deterministic place instead of being spread across adapters.
    Provenance fields are mandatory: a provider may not emit a record without
    saying where and when it came from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    source_transaction_id: str | None = None
    source_url: str | None = None
    deal_date: date | None = None
    area_sqm: float | None = None
    layout: str | None = None
    floor_bucket: str | None = None
    orientation: str | None = None
    built_year: int | None = None
    initial_listing_price_cny: float | None = None
    deal_price_cny: float | None = None
    unit_price_cny_sqm: float | None = None
    days_on_market: int | None = None
    raw_payload_ref: str | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
