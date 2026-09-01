from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from .common import FloorBucket, FrozenModel, ListingStatus


class Listing(FrozenModel):
    listing_id: str = Field(min_length=1)
    community_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_listing_id: str = Field(min_length=1)
    area_sqm: float | None = Field(default=None, gt=0)
    layout: str | None = None
    floor_bucket: FloorBucket = FloorBucket.UNKNOWN
    orientation: str | None = None
    built_year: int | None = Field(default=None, ge=1800, le=2200)
    building_type: str | None = None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    status: ListingStatus = ListingStatus.UNKNOWN

    @model_validator(mode="after")
    def validate_seen_range(self) -> "Listing":
        if self.first_seen_at > self.last_seen_at:
            raise ValueError("first_seen_at must be <= last_seen_at")
        return self


class ListingSnapshot(FrozenModel):
    listing_id: str = Field(min_length=1)
    snapshot_at: AwareDatetime
    list_price_cny: float = Field(gt=0)
    unit_price_cny_sqm: float | None = Field(default=None, gt=0)
    status: ListingStatus = ListingStatus.UNKNOWN
    source: str = Field(min_length=1)
    source_url: str | None = None
    raw_payload_ref: str | None = None
    parser_version: str = Field(min_length=1)


class ListingObservation(FrozenModel):
    listing: Listing
    snapshot: ListingSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> "ListingObservation":
        if self.listing.listing_id != self.snapshot.listing_id:
            raise ValueError("listing and snapshot listing_id must match")
        return self
