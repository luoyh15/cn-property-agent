from __future__ import annotations

from pydantic import Field, model_validator

from .common import FloorBucket, FrozenModel, SourceRef


class Community(FrozenModel):
    community_id: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    district: str | None = None
    subdistrict: str | None = None
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    built_year_min: int | None = Field(default=None, ge=1800, le=2200)
    built_year_max: int | None = Field(default=None, ge=1800, le=2200)
    building_types: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_years_and_coordinates(self) -> "Community":
        if self.built_year_min is not None and self.built_year_max is not None and self.built_year_min > self.built_year_max:
            raise ValueError("built_year_min must be <= built_year_max")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class PropertyUnit(FrozenModel):
    property_id: str = Field(min_length=1)
    community_id: str = Field(min_length=1)
    area_sqm: float | None = Field(default=None, gt=0)
    layout: str | None = None
    floor_bucket: FloorBucket = FloorBucket.UNKNOWN
    orientation: str | None = None
    built_year: int | None = Field(default=None, ge=1800, le=2200)
    building_type: str | None = None
    source_refs: tuple[SourceRef, ...] = ()


class EntityAlias(FrozenModel):
    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_entity_id: str = Field(min_length=1)
    provider_url: str | None = None
