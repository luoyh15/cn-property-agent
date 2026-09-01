from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from .common import FrozenModel, ResearchEventType, TransportMode


class MarketObservation(FrozenModel):
    observation_id: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    geography_type: str = Field(min_length=1)
    geography_code: str | None = None
    geography_name: str = Field(min_length=1)
    period_start: date
    period_end: date
    metric_name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_url: str | None = None
    publication_date: date | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
    raw_payload_ref: str | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "MarketObservation":
        if self.period_start > self.period_end:
            raise ValueError("period_start must be <= period_end")
        return self


class LandParcel(FrozenModel):
    parcel_id: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    name: str | None = None
    district: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    land_use: str | None = None
    site_area_sqm: float | None = Field(default=None, ge=0)
    residential_gfa_sqm: float | None = Field(default=None, ge=0)
    announced_at: date | None = None
    source: str = Field(min_length=1)
    source_url: str | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
    raw_payload_ref: str | None = None


class PlanningEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    district: str | None = None
    occurred_at: date | None = None
    published_at: date | None = None
    summary: str | None = None
    source: str = Field(min_length=1)
    source_url: str | None = None
    collected_at: AwareDatetime
    parser_version: str = Field(min_length=1)
    raw_payload_ref: str | None = None


class POI(FrozenModel):
    poi_id: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str | None = None
    source: str = Field(min_length=1)
    source_ref: str | None = None
    collected_at: AwareDatetime


class CommuteMetric(FrozenModel):
    commute_id: str = Field(min_length=1)
    origin_community_id: str = Field(min_length=1)
    destination_name: str = Field(min_length=1)
    destination_address: str | None = None
    destination_latitude: float | None = Field(default=None, ge=-90, le=90)
    destination_longitude: float | None = Field(default=None, ge=-180, le=180)
    mode: TransportMode
    duration_seconds: int = Field(ge=0)
    distance_m: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime
    source: str = Field(min_length=1)
    query_assumptions: dict[str, Any] = Field(default_factory=dict)


class ResearchEvent(FrozenModel):
    research_event_id: str = Field(min_length=1)
    event_type: ResearchEventType
    title: str = Field(min_length=1)
    summary: str | None = None
    occurred_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    city_code: str = Field(min_length=1)
    district: str | None = None
    community_id: str | None = None
    source_url: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    collected_at: AwareDatetime


class MetricObservation(FrozenModel):
    metric_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    as_of: AwareDatetime
    window: str | None = None
    value: float | None = None
    unit: str | None = None
    sample_size: int | None = Field(default=None, ge=0)
    algorithm_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    source_record_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class AnalysisRun(FrozenModel):
    analysis_run_id: str = Field(min_length=1)
    analysis_type: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    algorithm_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
