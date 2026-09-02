from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Iterator, Sequence

import pytest

from cn_property_agent.domain import MarketObservation
from cn_property_agent.providers import (
    MarketObservationFetchResult,
    MarketObservationProvider,
    fetch,
    protocols,
)
from cn_property_agent.services import (
    MarketObservationIngestionRequest,
    MarketObservationIngestionService,
    ProviderContractError,
    ProviderFetchError,
    market_observation_ingestion,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import MarketObservationRepository
from fakes import FakeMarketObservationProvider

CITY = "shanghai"
OTHER_CITY = "shenzhen"


@pytest.fixture
def repository() -> Iterator[MarketObservationRepository]:
    with DuckDBDatabase() as database:
        yield MarketObservationRepository(database.connection)


def build_service(
    repository: MarketObservationRepository,
    provider: MarketObservationProvider,
) -> MarketObservationIngestionService:
    return MarketObservationIngestionService(provider=provider, repository=repository)


def make_provider(
    observations: Sequence[MarketObservation] | MarketObservationFetchResult,
    *,
    city_code: str = CITY,
    error: Exception | None = None,
) -> FakeMarketObservationProvider:
    return FakeMarketObservationProvider({city_code: observations}, error=error)


def stored(repository: MarketObservationRepository, city_code: str = CITY) -> list[MarketObservation]:
    return repository.list_for_city(city_code)


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeMarketObservationProvider(), MarketObservationProvider)


@pytest.mark.asyncio
async def test_batch_persists_with_deterministic_counts_and_ids(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    observations = [
        market_observations["city_january"],
        market_observations["district_january_price"],
    ]
    provider = make_provider(observations)
    service = build_service(repository, provider)

    result = await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert result.city_code == CITY
    assert (result.source_observation_count, result.persisted_observation_count) == (2, 2)
    # Identifiers in the order the provider reported them, not in storage order.
    assert result.observation_ids == ("mo-0002", "mo-0004")
    assert result.observation_count == 2
    assert stored(repository) == observations
    # Exactly one provider call for one ingestion request.
    assert provider.calls == [(CITY, None, None, None)]


@pytest.mark.asyncio
async def test_request_narrowings_reach_the_provider_verbatim(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    provider = make_provider([market_observations["district_january_price"]])
    service = build_service(repository, provider)

    await service.ingest(
        MarketObservationIngestionRequest(
            city_code=CITY,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            geography_code="310115",
        )
    )

    assert provider.calls == [(CITY, date(2026, 1, 1), date(2026, 1, 31), "310115")]


def test_request_rejects_an_inverted_period() -> None:
    with pytest.raises(ValueError):
        MarketObservationIngestionRequest(
            city_code=CITY, start_date=date(2026, 3, 1), end_date=date(2026, 1, 31)
        )


@pytest.mark.asyncio
async def test_exact_replay_is_idempotent(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    """`observation_id` is the storage key, so replay rewrites the same rows."""
    observations = [
        market_observations["city_january"],
        market_observations["district_january_price"],
    ]
    service = build_service(repository, make_provider(observations))
    request = MarketObservationIngestionRequest(city_code=CITY)

    first = await service.ingest(request)
    after_first = stored(repository)
    second = await service.ingest(request)
    after_second = stored(repository)

    assert second == first
    assert after_second == after_first
    assert len(after_second) == 2


@pytest.mark.asyncio
async def test_revised_observation_overwrites_under_the_same_id(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    """A correction is one measurement republished, not a second identity."""
    original = market_observations["district_february_price"]
    corrected = original.model_copy(
        update={
            "value": 77450.0,
            "publication_date": date(2026, 4, 2),
            "source_url": "https://example.invalid/stats/2026-02/310115/revised",
            "parser_version": "market-fixture-v2",
        }
    )
    provider = make_provider([original])
    service = build_service(repository, provider)

    await service.ingest(MarketObservationIngestionRequest(city_code=CITY))
    provider.publish(CITY, [corrected])
    result = await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert result.observation_ids == (original.observation_id,)
    assert stored(repository) == [corrected]
    assert stored(repository)[0].value == 77450.0


@pytest.mark.asyncio
async def test_provenance_round_trips_unchanged(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    observations = [
        market_observations["city_quarterly"],
        market_observations["city_march_without_optionals"],
    ]
    service = build_service(repository, make_provider(observations))

    await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    by_id = {item.observation_id: item for item in stored(repository)}
    for observation in observations:
        persisted = by_id[observation.observation_id]
        assert persisted.source == observation.source
        assert persisted.source_url == observation.source_url
        assert persisted.publication_date == observation.publication_date
        assert persisted.collected_at == observation.collected_at
        assert persisted.parser_version == observation.parser_version
        assert persisted.raw_payload_ref == observation.raw_payload_ref
        assert persisted == observation

    # An absent geography code, URL, publication date or payload ref is not invented.
    sparse = by_id["mo-0007"]
    assert (sparse.geography_code, sparse.source_url, sparse.publication_date, sparse.raw_payload_ref) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_empty_provider_result_is_a_successful_empty_ingestion(
    repository: MarketObservationRepository,
) -> None:
    """A city that published nothing for the request is an answer, not an error."""
    provider = FakeMarketObservationProvider()
    service = build_service(repository, provider)

    result = await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert (result.source_observation_count, result.persisted_observation_count) == (0, 0)
    assert result.observation_ids == ()
    assert stored(repository) == []
    assert provider.calls == [(CITY, None, None, None)]


@pytest.mark.asyncio
async def test_provider_failure_is_explicit_and_writes_nothing(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    failure = RuntimeError("source unavailable")
    provider = make_provider([market_observations["city_january"]], error=failure)
    service = build_service(repository, provider)

    with pytest.raises(ProviderFetchError) as excinfo:
        await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert excinfo.value.__cause__ is failure
    assert excinfo.value.subject_id == CITY
    assert stored(repository) == []


@pytest.mark.asyncio
async def test_foreign_city_observation_fails_before_any_write(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    """A batch that answers about the wrong city is refused whole.

    The matching observation is listed first on purpose: validating row by row
    while persisting would already have written it before reaching the foreign
    one, leaving a batch half about one city and half about another.
    """
    matching = market_observations["city_january"]
    foreign = market_observations["other_city_january"]
    assert foreign.city_code != CITY

    service = build_service(repository, make_provider([matching, foreign]))

    with pytest.raises(ProviderContractError) as excinfo:
        await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert excinfo.value.subject_id == CITY
    assert foreign.observation_id in str(excinfo.value)
    assert foreign.city_code in str(excinfo.value)

    assert stored(repository) == []
    assert stored(repository, OTHER_CITY) == []


@pytest.mark.asyncio
async def test_stored_observation_cannot_be_moved_across_cities(
    repository: MarketObservationRepository,
    market_observations: dict[str, MarketObservation],
) -> None:
    """Storage keys observations by `observation_id` alone, so the guard protects them."""
    foreign = market_observations["other_city_january"]
    repository.upsert(foreign)

    service = build_service(repository, make_provider([foreign]))

    with pytest.raises(ProviderContractError):
        await service.ingest(MarketObservationIngestionRequest(city_code=CITY))

    assert stored(repository) == []
    assert stored(repository, OTHER_CITY) == [foreign]


FORBIDDEN_TOKENS = (
    "shanghai",
    "shenzhen",
    "lianjia",
    "beike",
    "cn_property_agent.config",
    "httpx",
    "requests",
    "playwright",
)


def module_source(module: ModuleType) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def absolute_internal_imports(source: str) -> set[str]:
    """Absolute `cn_property_agent` modules a source file imports.

    Relative imports inside the same package are not reported: they cannot
    cross a layer boundary.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module)
    return {name for name in imported if name.startswith("cn_property_agent")}


def test_service_is_source_independent() -> None:
    """The service must not reach below the provider/domain/storage boundary."""
    source = module_source(market_observation_ingestion)

    assert absolute_internal_imports(source) == {
        "cn_property_agent.domain",
        "cn_property_agent.providers",
        "cn_property_agent.storage.repositories",
    }
    assert not any(token in source.lower() for token in FORBIDDEN_TOKENS)


def test_provider_boundary_is_source_independent() -> None:
    """The protocol and its result shape may depend on the canonical domain only."""
    for module in (protocols, fetch):
        source = module_source(module)
        assert absolute_internal_imports(source) == {"cn_property_agent.domain"}
        assert not any(token in source.lower() for token in FORBIDDEN_TOKENS)
