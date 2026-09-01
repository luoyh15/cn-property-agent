from __future__ import annotations

from datetime import date

from pydantic import AwareDatetime, Field

from .common import FloorBucket, FrozenModel


class Transaction(FrozenModel):
    transaction_id: str = Field(min_length=1)
    community_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_transaction_id: str | None = None
    source_url: str | None = None
    deal_date: date
    area_sqm: float = Field(gt=0)
    layout: str | None = None
    floor_bucket: FloorBucket = FloorBucket.UNKNOWN
    orientation: str | None = None
    built_year: int | None = Field(default=None, ge=1800, le=2200)
    initial_listing_price_cny: float | None = Field(default=None, gt=0)
    deal_price_cny: float = Field(gt=0)
    unit_price_cny_sqm: float = Field(gt=0)
    days_on_market: int | None = Field(default=None, ge=0)
    raw_payload_ref: str | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
