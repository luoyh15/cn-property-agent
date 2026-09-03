"""Recorded Shanghai official market provider backed by a local JSON dataset.

This adapter replays official market rows that were captured earlier and written
to a file. It performs no network, browser or clock access at all: the capture
instant is part of the dataset, and field interpretation is delegated in full to
:func:`~cn_property_agent.providers.shanghai_official.parse_market_rows`.
Recorded before live is deliberate — the canonical mapping, identity and
provenance of this source are proven against a fixed input before any
acquisition transport exists.

Dataset format::

    {
      "city_code": "shanghai",
      "collected_at": "2026-04-08T01:30:00Z",
      "rows": [
        {
          "指标": "二手住宅成交套数",
          "地区": "浦东新区",
          "地区代码": "310115",       # optional: some tables name regions only
          "地区层级": "区",
          "统计周期": "2026-01",
          "数值": "1,820",
          "单位": "套",
          "发布日期": "2026-02-16",   # optional
          "来源链接": "https://...",  # optional
          "raw_payload_ref": "..."    # optional
        },
        ...
      ]
    }

``rows`` holds already-extracted official table rows, exactly what the parser
consumes. Provenance is row-level rather than batch-level: one capture may cover
several official tables published on different days, and a batch-level default
would attribute a publication date or a page to figures that never carried one.
What a row omits stays ``None``.

The subject is recorded, not inferred: the dataset names the city it was
captured for, and a request for any other city is refused rather than answered
with this city's series.

Failure boundary: a dataset that cannot be read as a batch — including a single
row the parser refuses — raises
:class:`~cn_property_agent.providers.shanghai_official.ShanghaiOfficialDatasetError`,
so an unusable recording can never reach the caller disguised as a city that
published nothing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from cn_property_agent.domain import MarketObservation
from cn_property_agent.providers import MarketObservationFetchResult

from .errors import (
    ShanghaiOfficialDatasetError,
    ShanghaiOfficialParseError,
    UnsupportedCityError,
)
from .market_parser import (
    SHANGHAI_CITY_CODE,
    ShanghaiOfficialParseContext,
    parse_market_rows,
)


class ShanghaiOfficialMarketDataset(BaseModel):
    """One recorded batch: which city was captured, when, and the rows seen.

    Unknown top-level keys are ignored so a dataset may carry the capture
    tooling's own notes; the keys this adapter does read are validated strictly,
    so absent or malformed batch metadata still fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    city_code: str = Field(min_length=1)
    collected_at: AwareDatetime
    rows: tuple[Any, ...]

    def parse_context(self) -> ShanghaiOfficialParseContext:
        """Batch-level subject and capture instant the rows cannot supply."""
        return ShanghaiOfficialParseContext(
            city_code=self.city_code,
            collected_at=self.collected_at,
        )


def load_market_dataset(path: Path | str) -> ShanghaiOfficialMarketDataset:
    """Read and validate one dataset file, or raise :class:`ShanghaiOfficialDatasetError`."""
    dataset_path = Path(path)
    try:
        text = dataset_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ShanghaiOfficialDatasetError(dataset_path, f"cannot be read: {error}") from error
    except UnicodeDecodeError as error:
        raise ShanghaiOfficialDatasetError(
            dataset_path, f"is not valid UTF-8: {error}"
        ) from error

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ShanghaiOfficialDatasetError(dataset_path, f"is not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise ShanghaiOfficialDatasetError(
            dataset_path,
            f"expected a JSON object at the top level, got {type(payload).__name__}",
        )

    try:
        return ShanghaiOfficialMarketDataset.model_validate(payload)
    except ValidationError as error:
        raise ShanghaiOfficialDatasetError(dataset_path, _format_validation_error(error)) from error


class RecordedShanghaiOfficialMarketProvider:
    """``MarketObservationProvider`` replaying a recorded official dataset.

    Request semantics, chosen to match
    :class:`~cn_property_agent.services.market_observation_query.MarketObservationQuery`
    so that asking a source and asking storage mean the same thing:

    - ``city_code`` is the subject boundary. Only Shanghai is served, and any
      other city raises :class:`UnsupportedCityError` rather than returning this
      city's series or an empty batch;
    - the date bounds are inclusive, independently optional, and constrain the
      observed period itself: ``start_date`` bounds ``period_start`` from below
      and ``end_date`` bounds ``period_end`` from above, so a window selects the
      observations wholly inside it and a monthly figure is never mixed with the
      quarter that merely overlaps the request;
    - ``geography_code`` selects observations published under exactly that code.
      Omitting it does not filter; it never means "match the rows without a
      code", which stay reachable through the unnarrowed request;
    - a supported city with nothing matching is a successful empty batch.

    Results are ordered ``period_start``, ``period_end``, ``observation_id``,
    the order stored observations are read back in. Because identity is derived
    from the measurement rather than from the recording, that order — and the
    whole result — is independent of how the rows happen to be laid out in the
    file.

    The file is re-read on every call, so the provider holds no cached state and
    repeated fetches of an unchanged dataset are identical.
    """

    def __init__(self, dataset_path: Path | str) -> None:
        self.dataset_path = Path(dataset_path)

    async def fetch_market_observations(
        self,
        *,
        city_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        geography_code: str | None = None,
    ) -> MarketObservationFetchResult:
        if city_code != SHANGHAI_CITY_CODE:
            raise UnsupportedCityError(city_code, supported_city_code=SHANGHAI_CITY_CODE)
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        if geography_code is not None and not geography_code.strip():
            raise ValueError("geography_code must not be blank when provided")

        dataset = load_market_dataset(self.dataset_path)
        if dataset.city_code != SHANGHAI_CITY_CODE:
            raise ShanghaiOfficialDatasetError(
                self.dataset_path,
                f"was recorded for city {dataset.city_code!r},"
                f" not the requested {city_code!r}",
            )

        try:
            observations = parse_market_rows(dataset.rows, context=dataset.parse_context())
        except ShanghaiOfficialParseError as error:
            raise ShanghaiOfficialDatasetError(self.dataset_path, str(error)) from error

        selected = [
            observation
            for observation in observations
            if _matches(
                observation,
                start_date=start_date,
                end_date=end_date,
                geography_code=geography_code,
            )
        ]
        return MarketObservationFetchResult.from_observations(sorted(selected, key=_series_order))


def _matches(
    observation: MarketObservation,
    *,
    start_date: date | None,
    end_date: date | None,
    geography_code: str | None,
) -> bool:
    if start_date is not None and observation.period_start < start_date:
        return False
    if end_date is not None and observation.period_end > end_date:
        return False
    return geography_code is None or observation.geography_code == geography_code


def _series_order(observation: MarketObservation) -> tuple[date, date, str]:
    return (observation.period_start, observation.period_end, observation.observation_id)


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
