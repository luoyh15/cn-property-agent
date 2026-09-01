from __future__ import annotations

from cn_property_agent.domain import EntityAlias
from cn_property_agent.services.community_resolver import (
    CommunityResolutionRequest,
    RepositoryCommunityResolver,
    ResolutionStatus,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import CommunityRepository


def test_resolver_marks_same_name_without_geography_ambiguous(communities) -> None:
    with DuckDBDatabase() as database:
        repository = CommunityRepository(database.connection)
        for community in communities[:2]:
            repository.upsert(community)
        resolver = RepositoryCommunityResolver(repository)

        result = resolver.resolve(
            CommunityResolutionRequest(query="阳光花园", city_code="shanghai")
        )

        assert result.status == ResolutionStatus.AMBIGUOUS
        assert len(result.candidates) == 2


def test_resolver_uses_district_to_disambiguate(communities) -> None:
    with DuckDBDatabase() as database:
        repository = CommunityRepository(database.connection)
        for community in communities[:2]:
            repository.upsert(community)
        resolver = RepositoryCommunityResolver(repository)

        result = resolver.resolve(
            CommunityResolutionRequest(
                query="阳光花园",
                city_code="shanghai",
                district="浦东新区",
            )
        )

        assert result.status == ResolutionStatus.RESOLVED
        assert result.community is not None
        assert result.community.community_id == "cm-sh-pd-001"


def test_resolver_prefers_provider_native_alias(communities) -> None:
    with DuckDBDatabase() as database:
        repository = CommunityRepository(database.connection)
        repository.upsert(communities[2])
        repository.upsert_alias(
            EntityAlias(
                entity_type="community",
                entity_id=communities[2].community_id,
                provider="fixture",
                provider_entity_id="native-rh-1",
            )
        )
        resolver = RepositoryCommunityResolver(repository)

        result = resolver.resolve(
            CommunityResolutionRequest(
                query="任意文本",
                city_code="shanghai",
                provider="fixture",
                provider_entity_id="native-rh-1",
            )
        )

        assert result.status == ResolutionStatus.RESOLVED
        assert result.confidence == 1.0
        assert result.community == communities[2]
