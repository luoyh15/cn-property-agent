from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cn_property_agent.domain import Community
from cn_property_agent.storage.repositories import CommunityRepository
from cn_property_agent.utils import haversine_distance_m, normalize_text


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class CommunityResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    district: str | None = None
    subdistrict: str | None = None
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    provider: str | None = None
    provider_entity_id: str | None = None

    @model_validator(mode="after")
    def validate_coordinates_and_alias(self) -> "CommunityResolutionRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if (self.provider is None) != (self.provider_entity_id is None):
            raise ValueError("provider and provider_entity_id must be provided together")
        return self


class CommunityResolutionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    community: Community
    confidence: float = Field(ge=0, le=1)
    reasons: tuple[str, ...] = ()


class CommunityResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ResolutionStatus
    community: Community | None = None
    confidence: float = Field(ge=0, le=1)
    candidates: tuple[CommunityResolutionCandidate, ...] = ()
    warnings: tuple[str, ...] = ()


class CommunityResolver(Protocol):
    def resolve(self, request: CommunityResolutionRequest) -> CommunityResolution: ...


class RepositoryCommunityResolver:
    """Conservative resolver over normalized communities and provider aliases."""

    def __init__(self, repository: CommunityRepository, *, geo_confirmation_radius_m: float = 750.0) -> None:
        self.repository = repository
        self.geo_confirmation_radius_m = geo_confirmation_radius_m

    def resolve(self, request: CommunityResolutionRequest) -> CommunityResolution:
        if request.provider and request.provider_entity_id:
            aliased = self.repository.find_by_alias(
                entity_type="community",
                provider=request.provider,
                provider_entity_id=request.provider_entity_id,
            )
            if aliased is not None:
                if aliased.city_code != request.city_code:
                    return CommunityResolution(
                        status=ResolutionStatus.NOT_FOUND,
                        confidence=0.0,
                        warnings=("provider alias exists but belongs to another city",),
                    )
                candidate = CommunityResolutionCandidate(
                    community=aliased,
                    confidence=1.0,
                    reasons=("exact provider-native id",),
                )
                return CommunityResolution(
                    status=ResolutionStatus.RESOLVED,
                    community=aliased,
                    confidence=1.0,
                    candidates=(candidate,),
                )

        normalized_query = normalize_text(request.query)
        assert normalized_query is not None
        candidates = self.repository.find_by_normalized_name(request.city_code, normalized_query)
        if not candidates:
            return CommunityResolution(
                status=ResolutionStatus.NOT_FOUND,
                confidence=0.0,
                warnings=("no exact normalized-name candidate",),
            )

        scored = [self._score(candidate, request) for candidate in candidates]
        scored.sort(key=lambda item: (-item.confidence, item.community.community_id))

        if len(scored) == 1:
            only = scored[0]
            return CommunityResolution(
                status=ResolutionStatus.RESOLVED,
                community=only.community,
                confidence=only.confidence,
                candidates=(only,),
            )

        best, second = scored[0], scored[1]
        if best.confidence >= 0.9 and best.confidence - second.confidence >= 0.15:
            return CommunityResolution(
                status=ResolutionStatus.RESOLVED,
                community=best.community,
                confidence=best.confidence,
                candidates=tuple(scored),
            )

        return CommunityResolution(
            status=ResolutionStatus.AMBIGUOUS,
            confidence=best.confidence,
            candidates=tuple(scored),
            warnings=("multiple same-name candidates remain; geographic confirmation required",),
        )

    def _score(
        self,
        community: Community,
        request: CommunityResolutionRequest,
    ) -> CommunityResolutionCandidate:
        score = 0.70
        reasons: list[str] = ["exact normalized name"]

        if request.district is not None and community.district is not None:
            if normalize_text(request.district) == normalize_text(community.district):
                score += 0.20
                reasons.append("district match")
            else:
                score -= 0.20
                reasons.append("district mismatch")

        if request.subdistrict is not None and community.subdistrict is not None:
            if normalize_text(request.subdistrict) == normalize_text(community.subdistrict):
                score += 0.08
                reasons.append("subdistrict match")
            else:
                score -= 0.10
                reasons.append("subdistrict mismatch")

        if request.address is not None and community.address is not None:
            if normalize_text(request.address) == normalize_text(community.address):
                score += 0.15
                reasons.append("address match")

        if (
            request.latitude is not None
            and request.longitude is not None
            and community.latitude is not None
            and community.longitude is not None
        ):
            distance = haversine_distance_m(
                request.latitude,
                request.longitude,
                community.latitude,
                community.longitude,
            )
            if distance <= self.geo_confirmation_radius_m:
                score += 0.15
                reasons.append(f"coordinate match within {distance:.0f}m")
            else:
                score -= 0.25
                reasons.append(f"coordinate mismatch ({distance:.0f}m)")

        return CommunityResolutionCandidate(
            community=community,
            confidence=max(0.0, min(1.0, score)),
            reasons=tuple(reasons),
        )
