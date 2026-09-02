from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from cn_property_agent.domain import MarketObservation
from cn_property_agent.services import (
    MarketObservationQuery,
    MarketObservationQueryService,
    market_observation_query,
)
from cn_property_agent.storage.database import DuckDBDatabase
from cn_property_agent.storage.repositories import MarketObservationRepository

CITY = "shanghai"
OTHER_CITY = "shenzhen"
DISTRICT_CODE = "310115"

# The whole seeded city series in the order the repository promises:
# period_start, then period_end, then observation_id.
CHRONOLOGICAL_IDS = ["mo-0002", "mo-0003", "mo-0004", "mo-0001", "mo-0005", "mo-0006", "mo-0007"]


@pytest.fixture
def seeded(
    market_observations: dict[str, MarketObservation],
) -> Iterator[tuple[MarketObservationQueryService, MarketObservationRepository]]:
    with DuckDBDatabase() as database:
        repository = MarketObservationRepository(database.connection)
        # Insert in an order unrelated to period or identifier, so the read
        # order can only come from the query.
        repository.upsert_many(reversed(list(market_observations.values())))
        yield MarketObservationQueryService(repository=repository), repository


def test_every_canonical_field_survives_the_round_trip(
    seeded, market_observations: dict[str, MarketObservation]
) -> None:
    """Geography, period, metric and provenance come back exactly as stored."""
    service, _ = seeded
    expected = market_observations["district_january_price"]

    result = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, metric_name="resale_unit_price_cny_sqm", end_date=date(2026, 1, 31))
    )

    assert result == (expected,)
    stored = result[0]
    assert stored.geography_type == expected.geography_type
    assert stored.geography_code == expected.geography_code
    assert stored.geography_name == expected.geography_name
    assert (stored.period_start, stored.period_end) == (date(2026, 1, 1), date(2026, 1, 31))
    assert (stored.value, stored.unit) == (expected.value, expected.unit)
    assert stored.source == expected.source
    assert stored.source_url == expected.source_url
    assert stored.publication_date == expected.publication_date
    assert stored.collected_at == expected.collected_at
    assert stored.parser_version == expected.parser_version
    assert stored.raw_payload_ref == expected.raw_payload_ref


def test_absent_optional_fields_stay_absent(
    seeded, market_observations: dict[str, MarketObservation]
) -> None:
    """A record without a source URL, publication date or payload ref is not repaired."""
    service, _ = seeded

    result = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, start_date=date(2026, 3, 1))
    )

    assert result == (market_observations["city_march_without_optionals"],)
    assert result[0].geography_code is None
    assert result[0].source_url is None
    assert result[0].publication_date is None
    assert result[0].raw_payload_ref is None


def test_ordering_is_chronological_with_stable_tie_break(seeded) -> None:
    service, _ = seeded
    query = MarketObservationQuery(city_code=CITY)

    first = service.get_market_observations(query)
    second = service.get_market_observations(query)

    assert [item.observation_id for item in first] == CHRONOLOGICAL_IDS
    # mo-0002 (Jan) precedes mo-0001 (Q1) although they share a period_start and
    # mo-0001 sorts first by identifier: period_end outranks the tie-break.
    assert [(item.period_start, item.period_end) for item in first[:4]] == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 1, 1), date(2026, 3, 31)),
    ]
    assert second == first


def test_query_returns_only_the_requested_city(seeded) -> None:
    service, _ = seeded

    result = service.get_market_observations(MarketObservationQuery(city_code=CITY))
    other = service.get_market_observations(MarketObservationQuery(city_code=OTHER_CITY))

    assert all(item.city_code == CITY for item in result)
    assert "mo-0008" not in {item.observation_id for item in result}
    # The excluded city shares metric name, unit, source and period.
    assert [item.observation_id for item in other] == ["mo-0008"]


def test_filters_narrow_by_geography_and_metric(seeded) -> None:
    service, _ = seeded

    by_type = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, geography_type="district")
    )
    by_code = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, geography_code=DISTRICT_CODE)
    )
    by_metric = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, metric_name="resale_price_index")
    )
    combined = service.get_market_observations(
        MarketObservationQuery(
            city_code=CITY,
            geography_type="district",
            geography_code=DISTRICT_CODE,
            metric_name="resale_unit_price_cny_sqm",
        )
    )

    assert [item.observation_id for item in by_type] == ["mo-0003", "mo-0004", "mo-0005", "mo-0006"]
    assert [item.observation_id for item in by_code] == ["mo-0003", "mo-0004", "mo-0005"]
    assert [item.observation_id for item in by_metric] == ["mo-0002", "mo-0001", "mo-0007"]
    assert [item.observation_id for item in combined] == ["mo-0004", "mo-0005"]


def test_period_bounds_select_periods_wholly_inside_the_window(seeded) -> None:
    """`start_date` bounds `period_start`; `end_date` bounds `period_end`."""
    service, _ = seeded

    first_quarter = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
    )
    january_only = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
    )

    assert [item.observation_id for item in first_quarter] == CHRONOLOGICAL_IDS
    # The quarterly figure is not wholly inside January, so it is excluded.
    assert [item.observation_id for item in january_only] == ["mo-0002", "mo-0003", "mo-0004"]


def test_no_matching_rows_is_an_empty_success(seeded) -> None:
    service, _ = seeded

    unknown_city = service.get_market_observations(MarketObservationQuery(city_code="city-never-ingested"))
    unknown_metric = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, metric_name="metric_never_published")
    )
    empty_window = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, start_date=date(2020, 1, 1), end_date=date(2020, 12, 31))
    )

    assert unknown_city == ()
    assert unknown_metric == ()
    assert empty_window == ()


def test_replaying_an_identical_observation_does_not_duplicate_it(
    seeded, market_observations: dict[str, MarketObservation]
) -> None:
    """`observation_id` is the storage key, so replay rewrites one row."""
    service, repository = seeded
    replayed = market_observations["district_february_price"]

    repository.upsert(replayed)
    repository.upsert(replayed)
    result = service.get_market_observations(MarketObservationQuery(city_code=CITY))

    ids = [item.observation_id for item in result]
    assert ids == CHRONOLOGICAL_IDS
    assert ids.count(replayed.observation_id) == 1
    assert next(item for item in result if item.observation_id == replayed.observation_id) == replayed


def test_republishing_the_same_id_overwrites_rather_than_forking_identity(
    seeded, market_observations: dict[str, MarketObservation]
) -> None:
    """A correction under the same identifier replaces every canonical field."""
    service, repository = seeded
    original = market_observations["district_february_price"]
    corrected = original.model_copy(
        update={
            "value": 77450.0,
            "publication_date": date(2026, 4, 2),
            "source_url": "https://example.invalid/stats/2026-02/310115/revised",
            "raw_payload_ref": "raw/market/2026-02-310115-revised.json",
            "parser_version": "market-fixture-v2",
        }
    )

    repository.upsert(corrected)
    result = service.get_market_observations(
        MarketObservationQuery(city_code=CITY, geography_code=DISTRICT_CODE)
    )

    assert [item.observation_id for item in result] == ["mo-0003", "mo-0004", "mo-0005"]
    stored = next(item for item in result if item.observation_id == original.observation_id)
    assert stored == corrected
    assert stored.value != original.value
    assert stored.parser_version == "market-fixture-v2"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"city_code": ""}, "city_code"),
        ({"city_code": "   "}, "city_code must not be blank"),
        ({"city_code": CITY, "geography_type": ""}, "geography_type must not be blank when provided"),
        ({"city_code": CITY, "geography_code": " "}, "geography_code must not be blank when provided"),
        ({"city_code": CITY, "metric_name": ""}, "metric_name must not be blank when provided"),
        (
            {"city_code": CITY, "start_date": date(2026, 3, 1), "end_date": date(2026, 1, 1)},
            "start_date must not be after end_date",
        ),
        ({"city_code": CITY, "source": "fixture_statistics_bureau"}, "source"),
    ],
)
def test_invalid_query_input_is_rejected(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        MarketObservationQuery(**kwargs)


def test_service_is_source_independent() -> None:
    """The query service must not reach below the storage/domain boundary."""
    source = Path(market_observation_query.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    internal = {name for name in imported if name.startswith("cn_property_agent")}
    assert internal == {
        "cn_property_agent.domain",
        "cn_property_agent.storage.repositories",
    }
    forbidden = (
        "lianjia",
        "beike",
        "cn_property_agent.config",
        "cn_property_agent.providers",
        "httpx",
        "requests",
        "playwright",
    )
    assert not any(token in source.lower() for token in forbidden)


def test_repository_and_service_carry_no_city_specific_logic() -> None:
    """City and metric names are data; neither layer may branch on them."""
    from cn_property_agent.storage import repositories

    service_source = Path(market_observation_query.__file__).read_text(encoding="utf-8").lower()
    repository_source = Path(repositories.__file__).read_text(encoding="utf-8").lower()

    for token in ("shanghai", "shenzhen", "310000", "310115"):
        assert token not in service_source
        assert token not in repository_source
