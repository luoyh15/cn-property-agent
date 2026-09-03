"""Shanghai official market-table parser.

This module owns *all* Shanghai-specific interpretation of an official market
table: its Chinese column names, indicator wording, region levels, period
notation and published units. It consumes an already extracted row mapping —
whatever the capture layer produced — plus batch context, and emits canonical
:class:`~cn_property_agent.domain.MarketObservation` records.

It performs no I/O: no HTTP, no HTML parsing, no clock access, no file access.
The collection instant is supplied by the caller through
:class:`ShanghaiOfficialParseContext`.

Canonical records rather than a provider DTO: a published figure carries its own
identity, geography and period, so nothing is left for a service to resolve —
see :class:`~cn_property_agent.providers.MarketObservationProvider`.

Only what the source states is mapped. A published indicator becomes a canonical
metric only if it appears in :data:`SERIES_CATALOGUE`, and a row must state the
unit that catalogue expects: a source that changes its unit is a failure rather
than an occasion to rescale a number on the source's behalf. Optional provenance
the row does not carry stays ``None`` and is never reconstructed.

Failure boundary: unlike the transaction and listing parsers, a row this parser
cannot interpret is *not* isolated as a rejection, because
:class:`~cn_property_agent.providers.MarketObservationFetchResult` has no
per-row rejection channel. Any row that cannot become a canonical observation
raises :class:`ShanghaiOfficialParseError` and fails the whole recorded batch,
so an unreadable table can never reach storage as a shorter but plausible
series. Rejection accounting for market rows is a change to the shared provider
envelope, not something this adapter invents for itself.
"""

from __future__ import annotations

import calendar
import math
import re
import unicodedata
from datetime import date
from typing import Any, Iterable, Mapping

from pydantic import AwareDatetime, Field, ValidationError

from cn_property_agent.domain import FrozenModel, MarketObservation
from cn_property_agent.utils import normalize_text, stable_id

from .errors import ShanghaiOfficialParseError

SHANGHAI_CITY_CODE = "shanghai"
"""The only city this adapter publishes; it matches the Shanghai city profile."""

SHANGHAI_OFFICIAL_SOURCE = "shanghai_official"
"""Provider name recorded on every observation this package emits."""

SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION = "shanghai-official-market-v1"

INDICATOR_FIELD = "指标"
GEOGRAPHY_NAME_FIELD = "地区"
GEOGRAPHY_CODE_FIELD = "地区代码"
GEOGRAPHY_LEVEL_FIELD = "地区层级"
PERIOD_FIELD = "统计周期"
VALUE_FIELD = "数值"
UNIT_FIELD = "单位"
PUBLICATION_DATE_FIELD = "发布日期"
SOURCE_URL_FIELD = "来源链接"
RAW_PAYLOAD_REF_FIELD = "raw_payload_ref"


class OfficialSeries(FrozenModel):
    """How one published indicator maps onto a canonical series.

    ``source_unit`` is the unit the source is expected to publish the indicator
    in. It is compared against every row rather than assumed, so a unit change
    at the source surfaces as a parse failure instead of a silently rescaled
    number.
    """

    metric_name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    source_unit: str = Field(min_length=1)


SERIES_CATALOGUE: Mapping[str, OfficialSeries] = {
    "二手住宅成交套数": OfficialSeries(
        metric_name="resale_transaction_count",
        unit="count",
        source_unit="套",
    ),
    "二手住宅成交均价": OfficialSeries(
        metric_name="resale_unit_price_cny_sqm",
        unit="cny_per_sqm",
        source_unit="元/平方米",
    ),
    "新建商品住宅销售价格指数": OfficialSeries(
        metric_name="new_home_price_index",
        unit="index_prior_month_100",
        source_unit="上月=100",
    ),
}
"""The published indicators this adapter understands, keyed by source wording."""

GEOGRAPHY_TYPES: Mapping[str, str] = {"市": "city", "区": "district"}
"""Region levels the official tables use, mapped to canonical geography types."""

_MONTH_PERIOD = re.compile(r"^(?P<year>\d{4})[-/年](?P<month>\d{1,2})月?$")
_QUARTER_PERIOD = re.compile(r"^(?P<year>\d{4})[-/年]?[Qq](?P<quarter>[1-4])$")

_DATE_PATTERNS = (
    re.compile(r"^(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})$"),
    re.compile(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?$"),
)


class ShanghaiOfficialParseContext(FrozenModel):
    """What the published rows cannot supply themselves.

    ``city_code`` is the city the batch was recorded for, and ``collected_at``
    is when it was read. Publication date, source URL and payload reference are
    row-level: one capture may cover several official tables published on
    different days, so a batch-level default would attribute a date or a page to
    figures that never carried it.
    """

    city_code: str = Field(min_length=1)
    collected_at: AwareDatetime


def build_observation_id(
    *,
    source: str,
    city_code: str,
    geography_type: str,
    geography_code: str | None,
    geography_name: str,
    metric_name: str,
    period_start: date,
    period_end: date,
) -> str:
    """Derive the stable identity of one published measurement.

    The identity is exactly what makes two publications the same measurement:
    who published it, about which subject and geography, for which series, over
    which period. Nothing volatile takes part — not the value, the publication
    date, the collection instant, the payload reference, the parser version or
    the row's position in the recorded table — so a corrected republication of a
    figure maps onto the same ``observation_id`` and overwrites the stored row
    instead of forking a second identity for one measurement.

    Geography is keyed by ``geography_code`` when the source publishes one,
    because a code survives naming changes. Tables that identify a region by
    name only fall back to the normalized ``geography_name``, which keeps the
    identity defined without inventing a code. The consequence is deliberate: if
    a source that published no code starts publishing one, that series gets a
    new identity, which is preferable to guessing that a name and a code refer
    to the same region.
    """
    geography_key = geography_code or normalize_text(geography_name) or geography_name
    return stable_id(
        "mo",
        source,
        city_code,
        geography_type,
        geography_key,
        metric_name,
        period_start.isoformat(),
        period_end.isoformat(),
    )


def parse_market_rows(
    rows: Iterable[Any],
    *,
    context: ShanghaiOfficialParseContext,
) -> tuple[MarketObservation, ...]:
    """Parse a batch of recorded official rows, in the order they were recorded.

    An empty batch is a successful empty result. A single unusable row fails the
    whole batch — see the module docstring — as does a batch that publishes the
    same measurement twice, because two rows sharing one ``observation_id``
    cannot both be true and the storage key would silently keep whichever was
    written last.
    """
    observations = tuple(
        parse_market_row(row, context=context, row_index=index)
        for index, row in enumerate(rows)
    )
    _require_distinct_identities(observations)
    return observations


def parse_market_row(
    row: Any,
    *,
    context: ShanghaiOfficialParseContext,
    row_index: int | None = None,
) -> MarketObservation:
    """Parse one recorded official row into a canonical observation."""
    if not isinstance(row, Mapping):
        raise ShanghaiOfficialParseError(
            f"expected a field mapping, got {type(row).__name__}", row_index=row_index
        )

    cells = _normalize_row_keys(row)
    indicator = _required(cells, INDICATOR_FIELD, row_index)
    series = SERIES_CATALOGUE.get(indicator)
    if series is None:
        known = ", ".join(sorted(SERIES_CATALOGUE))
        raise ShanghaiOfficialParseError(
            f"unknown indicator {indicator!r}; recorded indicators: {known}",
            field=INDICATOR_FIELD,
            row_index=row_index,
        )

    source_unit = _required(cells, UNIT_FIELD, row_index)
    if source_unit != series.source_unit:
        raise ShanghaiOfficialParseError(
            f"indicator {indicator!r} is published in {series.source_unit!r},"
            f" but this row states {source_unit!r}",
            field=UNIT_FIELD,
            row_index=row_index,
        )

    level = _required(cells, GEOGRAPHY_LEVEL_FIELD, row_index)
    geography_type = GEOGRAPHY_TYPES.get(level)
    if geography_type is None:
        known = ", ".join(sorted(GEOGRAPHY_TYPES))
        raise ShanghaiOfficialParseError(
            f"unknown region level {level!r}; recorded levels: {known}",
            field=GEOGRAPHY_LEVEL_FIELD,
            row_index=row_index,
        )

    geography_name = _required(cells, GEOGRAPHY_NAME_FIELD, row_index)
    geography_code = _optional(cells, GEOGRAPHY_CODE_FIELD, row_index)
    period_start, period_end = _parse_period(
        _required(cells, PERIOD_FIELD, row_index), row_index=row_index
    )
    value = _parse_value(_required(cells, VALUE_FIELD, row_index), row_index=row_index)
    publication_text = _optional(cells, PUBLICATION_DATE_FIELD, row_index)
    publication_date = (
        None
        if publication_text is None
        else _parse_date(publication_text, field=PUBLICATION_DATE_FIELD, row_index=row_index)
    )

    try:
        return MarketObservation(
            observation_id=build_observation_id(
                source=SHANGHAI_OFFICIAL_SOURCE,
                city_code=context.city_code,
                geography_type=geography_type,
                geography_code=geography_code,
                geography_name=geography_name,
                metric_name=series.metric_name,
                period_start=period_start,
                period_end=period_end,
            ),
            city_code=context.city_code,
            geography_type=geography_type,
            geography_code=geography_code,
            geography_name=geography_name,
            period_start=period_start,
            period_end=period_end,
            metric_name=series.metric_name,
            value=value,
            unit=series.unit,
            source=SHANGHAI_OFFICIAL_SOURCE,
            source_url=_optional(cells, SOURCE_URL_FIELD, row_index),
            publication_date=publication_date,
            collected_at=context.collected_at,
            parser_version=SHANGHAI_OFFICIAL_MARKET_PARSER_VERSION,
            raw_payload_ref=_optional(cells, RAW_PAYLOAD_REF_FIELD, row_index),
        )
    except ValidationError as error:
        raise ShanghaiOfficialParseError(
            _format_validation_error(error), row_index=row_index
        ) from error


def _require_distinct_identities(observations: tuple[MarketObservation, ...]) -> None:
    seen: dict[str, int] = {}
    for index, observation in enumerate(observations):
        first = seen.setdefault(observation.observation_id, index)
        if first != index:
            raise ShanghaiOfficialParseError(
                f"repeats the measurement already recorded by row {first}"
                f" ({observation.metric_name} for {observation.geography_name},"
                f" {observation.period_start}..{observation.period_end})",
                row_index=index,
            )


def _normalize_row_keys(row: Mapping[str, Any]) -> dict[str, Any]:
    return {_normalize_key(key): value for key, value in row.items()}


def _normalize_key(key: object) -> str:
    """Normalize a column name so half/full-width variants collide."""
    return "".join(unicodedata.normalize("NFKC", str(key)).split()).casefold()


def _cell(cells: Mapping[str, Any], column: str, row_index: int | None) -> str | None:
    """Normalized text of one cell, or ``None`` when the source omits it.

    An absent column and an empty one mean the same thing — the source published
    nothing there — which is why an omission stays ``None`` for optional
    provenance and fails loudly for a column an observation is defined by. A
    cell that is not a published scalar at all is a malformed recording rather
    than an omission, so it raises instead of reading as absent.
    """
    value = cells.get(_normalize_key(column))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ShanghaiOfficialParseError(
            f"expected a published scalar, got {type(value).__name__}",
            field=column,
            row_index=row_index,
        )
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return text or None


def _required(cells: Mapping[str, Any], column: str, row_index: int | None) -> str:
    text = _cell(cells, column, row_index)
    if text is None:
        raise ShanghaiOfficialParseError(
            "is missing or empty", field=column, row_index=row_index
        )
    return text


def _optional(cells: Mapping[str, Any], column: str, row_index: int | None) -> str | None:
    return _cell(cells, column, row_index)


def _parse_period(text: str, *, row_index: int | None) -> tuple[date, date]:
    """``"2026-01"`` / ``"2026年1月"`` / ``"2026Q1"`` → the closed period it covers.

    Only the whole calendar periods the official tables publish are accepted.
    A period that does not state its own boundaries is refused rather than
    widened or narrowed to a guessed window.
    """
    month = _MONTH_PERIOD.match(text)
    if month is not None:
        return _calendar_span(
            int(month["year"]), int(month["month"]), int(month["month"]), text, row_index
        )
    quarter = _QUARTER_PERIOD.match(text)
    if quarter is not None:
        last_month = 3 * int(quarter["quarter"])
        return _calendar_span(
            int(quarter["year"]), last_month - 2, last_month, text, row_index
        )
    raise ShanghaiOfficialParseError(
        f"unrecognized reporting period {text!r}; expected a month (2026-01) or a"
        " quarter (2026Q1)",
        field=PERIOD_FIELD,
        row_index=row_index,
    )


def _calendar_span(
    year: int,
    first_month: int,
    last_month: int,
    text: str,
    row_index: int | None,
) -> tuple[date, date]:
    try:
        start = date(year, first_month, 1)
    except ValueError as error:
        raise ShanghaiOfficialParseError(
            f"{text!r} is not a calendar period: {error}",
            field=PERIOD_FIELD,
            row_index=row_index,
        ) from error
    end = date(year, last_month, calendar.monthrange(year, last_month)[1])
    return start, end


def _parse_value(text: str, *, row_index: int | None) -> float:
    """Parse the published figure, allowing the thousands separators tables use."""
    try:
        value = float(text.replace(",", ""))
    except ValueError as error:
        raise ShanghaiOfficialParseError(
            f"{text!r} is not a number", field=VALUE_FIELD, row_index=row_index
        ) from error
    if not math.isfinite(value):
        raise ShanghaiOfficialParseError(
            f"{text!r} is not a finite number", field=VALUE_FIELD, row_index=row_index
        )
    return value


def _parse_date(text: str, *, field: str, row_index: int | None) -> date:
    """Accept the full calendar dates the official pages carry; refuse partial ones."""
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        try:
            return date(int(match["year"]), int(match["month"]), int(match["day"]))
        except ValueError as error:
            raise ShanghaiOfficialParseError(
                f"{text!r} is not a calendar date: {error}", field=field, row_index=row_index
            ) from error
    raise ShanghaiOfficialParseError(
        f"unrecognized date format {text!r}", field=field, row_index=row_index
    )


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
