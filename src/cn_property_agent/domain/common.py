from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    """Immutable canonical value object."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class FloorBucket(StrEnum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"
    UNKNOWN = "unknown"


class ListingStatus(StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    OFF_MARKET = "off_market"
    UNKNOWN = "unknown"


class TransportMode(StrEnum):
    DRIVING = "driving"
    TRANSIT = "transit"
    WALKING = "walking"
    CYCLING = "cycling"


class ResearchEventType(StrEnum):
    POLICY = "policy"
    PLANNING = "planning"
    MARKET = "market"
    COMMUNITY = "community"
    OTHER = "other"


class SourceRef(FrozenModel):
    provider: str = Field(min_length=1)
    provider_entity_id: str | None = None
    provider_url: str | None = None


class Provenance(FrozenModel):
    source: str = Field(min_length=1)
    source_url: str | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
    raw_payload_ref: str | None = None


class GeoPoint(FrozenModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GeocodeResult(FrozenModel):
    city_code: str = Field(min_length=1)
    formatted_address: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    confidence: float = Field(ge=0, le=1)
    source: str
    source_ref: str | None = None


JsonScalar = str | int | float | bool | None
JsonObject = dict[str, JsonScalar]
