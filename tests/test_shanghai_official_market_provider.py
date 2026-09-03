from __future__ import annotations

import ast
import json
import socket
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import pytest

from cn_property_agent.domain import MarketObservation
from cn_property_agent.providers import MarketObservationProvider
from cn_property_agent.providers.shanghai_official import (
    SHANGHAI_CITY_CODE,
    SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION,
    SHANGHAI_OFFICIAL_SOURCE,
    RecordedShanghaiOfficialMarketProvider,
    ShanghaiOfficialDatasetError,
    UnsupportedCityError,
    build_observation_id,
)
from cn_property_agent.services import (
    MarketObservationIngestionRequest,
    MarketObservationIngestionService,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import MarketObservationRepository

DATASET_FIXTURE = Path(__file__).parent / "fixtures" / "shanghai_official_market.json"

COLLECTED_AT = datetime(2026, 4, 8, 1, 30, tzinfo=timezone.utc)

JANUARY = (date(2026, 1, 1), date(2026, 1, 31))
FEBRUARY = (date(2026, 2, 1), date(2026, 2, 28))
FIRST_QUARTER = (date(2026, 1, 1), date(2026, 3, 31))


@pytest.fixture
def dataset_payload() -> dict[str, Any]:
    return json.loads(DATASET_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def provider() -> RecordedShanghaiOfficialMarketProvider:
    return RecordedShanghaiOfficialMarketProvider(DATASET_FIXTURE)


@pytest.fixture
def repository() -> Iterator[MarketObservationRepository]:
    with DuckDBDatabase() as database:
        yield MarketObservationRepository(database.connection)


def write_dataset(tmp_path: Path, payload: Any, name: str = "dataset.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def with_row(payload: dict[str, Any], index: int, changes: dict[str, Any]) -> dict[str, Any]:
    """The dataset with one recorded row's columns added/overwritten."""
    rows = [dict(row) for row in payload["rows"]]
    rows[index] = {**rows[index], **changes}
    return {**payload, "rows": rows}


def without_column(payload: dict[str, Any], index: int, column: str) -> dict[str, Any]:
    rows = [dict(row) for row in payload["rows"]]
    rows[index] = {key: value for key, value in rows[index].items() if key != column}
    return {**payload, "rows": rows}


def find(
    observations: Sequence[MarketObservation],
    *,
    metric_name: str,
    geography_name: str,
    period_start: date,
) -> MarketObservation:
    return next(
        item
        for item in observations
        if item.metric_name == metric_name
        and item.geography_name == geography_name
        and item.period_start == period_start
    )


async def fetch_all(provider: RecordedShanghaiOfficialMarketProvider) -> tuple[MarketObservation, ...]:
    result = await provider.fetch_market_observations(city_code=SHANGHAI_CITY_CODE)
    return result.observations


def test_provider_satisfies_the_market_observation_provider_protocol(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    assert isinstance(provider, MarketObservationProvider)


@pytest.mark.asyncio
async def test_recorded_dataset_parses_into_canonical_observations(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    result = await provider.fetch_market_observations(city_code=SHANGHAI_CITY_CODE)

    assert result.observation_count == 8
    # Chronological, and the shorter period sorts before the quarter starting
    # with it — the order stored observations are read back in.
    assert [(item.period_start, item.period_end) for item in result.observations] == [
        *[JANUARY] * 5,
        FIRST_QUARTER,
        *[FEBRUARY] * 2,
    ]

    observation = find(
        result.observations,
        metric_name="resale_unit_price_cny_sqm",
        geography_name="浦东新区",
        period_start=date(2026, 1, 1),
    )
    assert observation == MarketObservation(
        observation_id=observation.observation_id,
        city_code=SHANGHAI_CITY_CODE,
        geography_type="district",
        geography_code="310115",
        geography_name="浦东新区",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        metric_name="resale_unit_price_cny_sqm",
        value=78_000.0,
        unit="cny_per_sqm",
        source=SHANGHAI_OFFICIAL_SOURCE,
        source_url="https://example.invalid/shanghai-official/resale/2026-01",
        publication_date=date(2026, 2, 16),
        collected_at=COLLECTED_AT,
        parser_version=SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION,
        raw_payload_ref="fixture://shanghai-official/resale/2026-01",
    )


@pytest.mark.asyncio
async def test_published_indicators_map_to_their_canonical_series(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    observations = await fetch_all(provider)

    # 套数 with a thousands separator, a 年/月 period and a 年月日 publication date.
    count = find(
        observations,
        metric_name="resale_transaction_count",
        geography_name="上海市",
        period_start=date(2026, 1, 1),
    )
    assert (count.value, count.unit, count.geography_type) == (15_220.0, "count", "city")
    assert (count.period_end, count.publication_date) == (date(2026, 1, 31), date(2026, 2, 16))

    # A quarterly row states its own boundaries.
    quarterly = find(
        observations,
        metric_name="resale_unit_price_cny_sqm",
        geography_name="上海市",
        period_start=date(2026, 1, 1),
    )
    assert (quarterly.period_start, quarterly.period_end) == FIRST_QUARTER
    assert (quarterly.value, quarterly.unit) == (71_500.0, "cny_per_sqm")

    # A numeric cell, and the unit the canonical series is expressed in.
    index = find(
        observations,
        metric_name="new_home_price_index",
        geography_name="上海市",
        period_start=date(2026, 1, 1),
    )
    assert (index.value, index.unit) == (100.4, "index_prior_month_100")


@pytest.mark.asyncio
async def test_absent_optional_fields_are_preserved_rather_than_invented(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    observations = await fetch_all(provider)

    # The index table names its region without a code and carries no publication
    # date, URL or payload reference: none of them is reconstructed.
    index = find(
        observations,
        metric_name="new_home_price_index",
        geography_name="上海市",
        period_start=date(2026, 1, 1),
    )
    assert (index.geography_code, index.source_url, index.publication_date, index.raw_payload_ref) == (
        None,
        None,
        None,
        None,
    )
    # What the batch does supply is still recorded.
    assert index.collected_at == COLLECTED_AT
    assert index.source == SHANGHAI_OFFICIAL_SOURCE


@pytest.mark.asyncio
async def test_result_is_independent_of_the_recorded_row_order(
    tmp_path: Path,
    provider: RecordedShanghaiOfficialMarketProvider,
    dataset_payload: dict[str, Any],
) -> None:
    reversed_rows = write_dataset(
        tmp_path, {**dataset_payload, "rows": list(reversed(dataset_payload["rows"]))}
    )
    rotated_rows = write_dataset(
        tmp_path,
        {**dataset_payload, "rows": dataset_payload["rows"][3:] + dataset_payload["rows"][:3]},
        name="rotated.json",
    )

    expected = await fetch_all(provider)

    assert await fetch_all(RecordedShanghaiOfficialMarketProvider(reversed_rows)) == expected
    assert await fetch_all(RecordedShanghaiOfficialMarketProvider(rotated_rows)) == expected


@pytest.mark.asyncio
async def test_replaying_the_same_dataset_is_deterministic(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    assert await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE
    ) == await provider.fetch_market_observations(city_code=SHANGHAI_CITY_CODE)


@pytest.mark.asyncio
async def test_corrected_publication_keeps_the_same_observation_id(
    tmp_path: Path,
    provider: RecordedShanghaiOfficialMarketProvider,
    dataset_payload: dict[str, Any],
) -> None:
    """A revision is one measurement republished, not a second identity."""
    corrected_payload = with_row(
        dataset_payload,
        0,
        {
            "数值": "77,450",
            "发布日期": "2026-04-02",
            "来源链接": "https://example.invalid/shanghai-official/resale/2026-02/revised",
            "raw_payload_ref": "fixture://shanghai-official/resale/2026-02/revised",
        },
    )
    # Re-captured later, too: the collection instant must not touch identity.
    corrected_payload = {**corrected_payload, "collected_at": "2026-04-09T01:30:00Z"}
    corrected = RecordedShanghaiOfficialMarketProvider(write_dataset(tmp_path, corrected_payload))

    original_observation = find(
        await fetch_all(provider),
        metric_name="resale_unit_price_cny_sqm",
        geography_name="浦东新区",
        period_start=date(2026, 2, 1),
    )
    corrected_observation = find(
        await fetch_all(corrected),
        metric_name="resale_unit_price_cny_sqm",
        geography_name="浦东新区",
        period_start=date(2026, 2, 1),
    )

    assert corrected_observation.observation_id == original_observation.observation_id
    assert (original_observation.value, corrected_observation.value) == (77_200.0, 77_450.0)
    assert corrected_observation.publication_date == date(2026, 4, 2)
    assert corrected_observation.source_url.endswith("/revised")


def test_observation_id_is_built_from_the_measurement_alone() -> None:
    identity = {
        "source": SHANGHAI_OFFICIAL_SOURCE,
        "city_code": SHANGHAI_CITY_CODE,
        "geography_type": "district",
        "geography_code": "310115",
        "geography_name": "浦东新区",
        "metric_name": "resale_unit_price_cny_sqm",
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 1, 31),
    }

    assert build_observation_id(**identity) == build_observation_id(**identity)
    # A renamed region under an unchanged code is the same series.
    assert build_observation_id(**{**identity, "geography_name": "浦东区"}) == build_observation_id(
        **identity
    )

    for changed in (
        {"source": "other_official"},
        {"city_code": "shenzhen"},
        {"geography_type": "city"},
        {"geography_code": "310112"},
        {"metric_name": "resale_transaction_count"},
        {"period_start": date(2026, 2, 1)},
        {"period_end": date(2026, 3, 31)},
    ):
        assert build_observation_id(**{**identity, **changed}) != build_observation_id(**identity)


def test_observation_id_falls_back_to_the_region_name_without_a_code() -> None:
    identity = {
        "source": SHANGHAI_OFFICIAL_SOURCE,
        "city_code": SHANGHAI_CITY_CODE,
        "geography_type": "city",
        "geography_name": "上海市",
        "metric_name": "new_home_price_index",
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 1, 31),
    }

    without_code = build_observation_id(**identity, geography_code=None)

    assert without_code == build_observation_id(**identity, geography_code=None)
    # A code is preferred when the source publishes one, so the two differ.
    assert without_code != build_observation_id(**identity, geography_code="310000")
    assert without_code != build_observation_id(
        **{**identity, "geography_name": "北京市"}, geography_code=None
    )


@pytest.mark.asyncio
async def test_date_window_selects_the_periods_wholly_inside_it(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    january = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )
    # The quarter overlapping January is not a January figure.
    assert {(item.period_start, item.period_end) for item in january.observations} == {JANUARY}
    assert january.observation_count == 5

    quarter = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
    )
    assert quarter.observation_count == 8

    # Each bound narrows independently.
    from_february = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE, start_date=date(2026, 2, 1)
    )
    assert {(item.period_start, item.period_end) for item in from_february.observations} == {FEBRUARY}


@pytest.mark.asyncio
async def test_geography_narrowing_selects_exactly_that_code(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    pudong = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE, geography_code="310115"
    )

    assert {item.geography_name for item in pudong.observations} == {"浦东新区"}
    assert pudong.observation_count == 3

    # Narrowings compose.
    pudong_january = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE,
        geography_code="310115",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )
    assert {item.metric_name for item in pudong_january.observations} == {
        "resale_transaction_count",
        "resale_unit_price_cny_sqm",
    }

    # Asking for a code never returns the rows the source published without one.
    city = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE, geography_code="310000"
    )
    assert all(item.geography_code == "310000" for item in city.observations)
    assert "new_home_price_index" not in {item.metric_name for item in city.observations}


@pytest.mark.asyncio
async def test_supported_city_without_matches_is_a_successful_empty_batch(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    unpublished_window = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE,
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
    )
    unknown_district = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE, geography_code="310120"
    )

    assert unpublished_window.observations == ()
    assert unpublished_window.observation_count == 0
    assert unknown_district.observations == ()


@pytest.mark.asyncio
async def test_unsupported_city_is_refused_explicitly(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    with pytest.raises(UnsupportedCityError) as error:
        await provider.fetch_market_observations(city_code="shenzhen")

    assert error.value.city_code == "shenzhen"
    assert error.value.supported_city_code == SHANGHAI_CITY_CODE


@pytest.mark.asyncio
async def test_dataset_recorded_for_another_city_is_refused(
    tmp_path: Path,
    dataset_payload: dict[str, Any],
) -> None:
    path = write_dataset(tmp_path, {**dataset_payload, "city_code": "shenzhen"})

    with pytest.raises(ShanghaiOfficialDatasetError, match="not the requested"):
        await RecordedShanghaiOfficialMarketProvider(path).fetch_market_observations(
            city_code=SHANGHAI_CITY_CODE
        )


@pytest.mark.asyncio
async def test_invalid_request_bounds_are_rejected(
    provider: RecordedShanghaiOfficialMarketProvider,
) -> None:
    with pytest.raises(ValueError):
        await provider.fetch_market_observations(
            city_code=SHANGHAI_CITY_CODE,
            start_date=date(2026, 3, 1),
            end_date=date(2026, 1, 31),
        )
    with pytest.raises(ValueError):
        await provider.fetch_market_observations(city_code=SHANGHAI_CITY_CODE, geography_code=" ")


@pytest.mark.parametrize(
    ("case", "mutate", "expected"),
    [
        (
            "unknown_indicator",
            lambda payload: with_row(payload, 4, {"指标": "二手住宅去化周期"}),
            "unknown indicator",
        ),
        (
            "unit_changed_at_the_source",
            lambda payload: with_row(payload, 5, {"单位": "元/平米"}),
            "is published in",
        ),
        (
            "unknown_region_level",
            lambda payload: with_row(payload, 5, {"地区层级": "街道"}),
            "unknown region level",
        ),
        (
            "period_not_a_calendar_period",
            lambda payload: with_row(payload, 5, {"统计周期": "2026-13"}),
            "is not a calendar period",
        ),
        (
            "period_not_recognized",
            lambda payload: with_row(payload, 5, {"统计周期": "2026上半年"}),
            "unrecognized reporting period",
        ),
        (
            "value_not_published",
            lambda payload: with_row(payload, 5, {"数值": "暂无数据"}),
            "is not a number",
        ),
        (
            "value_not_finite",
            lambda payload: with_row(payload, 5, {"数值": "inf"}),
            "is not a finite number",
        ),
        ("value_missing", lambda payload: without_column(payload, 5, "数值"), "is missing or empty"),
        ("region_missing", lambda payload: without_column(payload, 5, "地区"), "is missing or empty"),
        ("unit_missing", lambda payload: without_column(payload, 5, "单位"), "is missing or empty"),
        (
            "publication_date_partial",
            lambda payload: with_row(payload, 5, {"发布日期": "2026-02"}),
            "unrecognized date format",
        ),
        (
            "cell_is_not_a_scalar",
            lambda payload: with_row(payload, 5, {"来源链接": ["a", "b"]}),
            "expected a published scalar",
        ),
        (
            "row_is_not_a_mapping",
            lambda payload: {**payload, "rows": [*payload["rows"], "78,000"]},
            "expected a field mapping",
        ),
        (
            "same_measurement_recorded_twice",
            lambda payload: {
                **payload,
                "rows": [*payload["rows"], {**payload["rows"][5], "数值": "78,100"}],
            },
            "repeats the measurement already recorded by row 5",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_row_that_cannot_be_made_canonical_fails_the_recorded_fetch(
    tmp_path: Path,
    dataset_payload: dict[str, Any],
    case: str,
    mutate: Callable[[dict[str, Any]], Any],
    expected: str,
) -> None:
    """No malformed row is invented into a value or quietly dropped."""
    path = write_dataset(tmp_path, mutate(dataset_payload))

    with pytest.raises(ShanghaiOfficialDatasetError) as error:
        await RecordedShanghaiOfficialMarketProvider(path).fetch_market_observations(
            city_code=SHANGHAI_CITY_CODE
        )

    # The failure names the recorded row and why it could not be made canonical.
    assert error.value.path == path
    assert expected in str(error.value)
    assert "row " in str(error.value)


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("top_level_array", lambda payload: payload["rows"]),
        ("rows_missing", lambda payload: {k: v for k, v in payload.items() if k != "rows"}),
        ("rows_not_an_array", lambda payload: {**payload, "rows": {"0": payload["rows"][0]}}),
        ("city_code_missing", lambda payload: {k: v for k, v in payload.items() if k != "city_code"}),
        ("city_code_blank", lambda payload: {**payload, "city_code": ""}),
        (
            "collected_at_missing",
            lambda payload: {k: v for k, v in payload.items() if k != "collected_at"},
        ),
        ("collected_at_naive", lambda payload: {**payload, "collected_at": "2026-04-08T01:30:00"}),
        ("collected_at_malformed", lambda payload: {**payload, "collected_at": "not-a-timestamp"}),
    ],
)
@pytest.mark.asyncio
async def test_invalid_dataset_never_looks_like_an_empty_success(
    tmp_path: Path,
    dataset_payload: dict[str, Any],
    case: str,
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    path = write_dataset(tmp_path, mutate(dataset_payload))

    with pytest.raises(ShanghaiOfficialDatasetError) as error:
        await RecordedShanghaiOfficialMarketProvider(path).fetch_market_observations(
            city_code=SHANGHAI_CITY_CODE
        )

    assert error.value.path == path


@pytest.mark.asyncio
async def test_unreadable_dataset_raises_instead_of_returning_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    for path in (missing, broken):
        with pytest.raises(ShanghaiOfficialDatasetError):
            await RecordedShanghaiOfficialMarketProvider(path).fetch_market_observations(
                city_code=SHANGHAI_CITY_CODE
            )


@pytest.mark.asyncio
async def test_empty_recorded_rows_is_a_successful_empty_fetch(
    tmp_path: Path,
    dataset_payload: dict[str, Any],
) -> None:
    path = write_dataset(tmp_path, {**dataset_payload, "rows": []})

    result = await RecordedShanghaiOfficialMarketProvider(path).fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE
    )

    assert result.observations == ()


@pytest.mark.asyncio
async def test_provider_reads_the_file_and_never_the_network(
    provider: RecordedShanghaiOfficialMarketProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a recorded provider must not open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    result = await provider.fetch_market_observations(city_code=SHANGHAI_CITY_CODE)

    assert result.observation_count == 8


@pytest.mark.asyncio
async def test_recorded_observations_persist_idempotently_with_their_provenance(
    provider: RecordedShanghaiOfficialMarketProvider,
    repository: MarketObservationRepository,
) -> None:
    """One integration pass through the source-independent ingestion boundary."""
    service = MarketObservationIngestionService(provider=provider, repository=repository)
    request = MarketObservationIngestionRequest(
        city_code=SHANGHAI_CITY_CODE,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )

    first = await service.ingest(request)
    stored_once = repository.list_for_city(SHANGHAI_CITY_CODE)
    second = await service.ingest(request)
    stored_twice = repository.list_for_city(SHANGHAI_CITY_CODE)

    assert (first.source_observation_count, first.persisted_observation_count) == (5, 5)
    assert first.observation_count == 5
    # Stable identity plus an `observation_id` primary key: a replay rewrites
    # the same rows rather than forking the series.
    assert second == first
    assert stored_twice == stored_once
    assert len(stored_twice) == 5

    fetched = await provider.fetch_market_observations(
        city_code=SHANGHAI_CITY_CODE, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert stored_twice == list(fetched.observations)

    # Provenance survives acquisition and storage untouched, absences included.
    index = find(
        stored_twice,
        metric_name="new_home_price_index",
        geography_name="上海市",
        period_start=date(2026, 1, 1),
    )
    assert index.source == SHANGHAI_OFFICIAL_SOURCE
    assert index.parser_version == SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION
    assert index.collected_at == COLLECTED_AT
    assert (index.source_url, index.publication_date, index.raw_payload_ref) == (None, None, None)

    priced = find(
        stored_twice,
        metric_name="resale_unit_price_cny_sqm",
        geography_name="闵行区",
        period_start=date(2026, 1, 1),
    )
    assert priced.source_url == "https://example.invalid/shanghai-official/resale/2026-01"
    assert priced.publication_date == date(2026, 2, 16)
    assert priced.raw_payload_ref == "fixture://shanghai-official/resale/2026-01"


SOURCE_INDEPENDENT_PACKAGES = ("services", "domain", "storage", "analytics")

PROVIDER_PACKAGE = "cn_property_agent.providers.shanghai_official"


def package_sources(package: str) -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "src" / "cn_property_agent" / package
    return sorted(root.rglob("*.py"))


def absolute_internal_imports(source: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module)
    return {name for name in imported if name.startswith("cn_property_agent")}


def test_shanghai_specifics_stay_inside_the_provider_package() -> None:
    """No source-independent layer may know that this adapter exists."""
    for package in SOURCE_INDEPENDENT_PACKAGES:
        modules = package_sources(package)
        assert modules
        for module in modules:
            source = module.read_text(encoding="utf-8")
            assert "shanghai" not in source.lower(), module
            assert not any(
                name.startswith(PROVIDER_PACKAGE) for name in absolute_internal_imports(source)
            ), module


def test_provider_package_depends_on_the_canonical_boundary_only() -> None:
    """The adapter may reach the domain and the provider contracts, nothing else."""
    for module in package_sources("providers/shanghai_official"):
        imported = absolute_internal_imports(module.read_text(encoding="utf-8"))
        assert imported <= {
            "cn_property_agent.domain",
            "cn_property_agent.providers",
            "cn_property_agent.utils",
        }, module
