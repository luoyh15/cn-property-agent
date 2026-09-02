"""Lianjia transaction field parser.

This module owns *all* Lianjia-specific interpretation of transaction fields:
Chinese field names, 万元/㎡ units, floor wording. It consumes an already
extracted mapping (whatever the transport/extraction layer produced) plus
provenance context, and emits source-independent
:class:`~cn_property_agent.providers.RawTransactionRecord` values.

It performs no I/O: no HTTP, no HTML parsing, no clock access. The collection
timestamp is supplied by the caller through :class:`LianjiaParseContext`.

Failure boundary: this parser rejects a row only when a source value cannot be
interpreted at all (or provider-native identity is missing). Plausibility —
positive prices, dates in range, total/unit price consistency — belongs to the
canonical data-quality gates in
:mod:`cn_property_agent.services.transaction_normalization`.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from pydantic import AwareDatetime, ValidationError

from cn_property_agent.domain import FrozenModel
from cn_property_agent.providers import (
    FieldParseError,
    ParseRejection,
    ParseRejectionReason,
    ParseResult,
    RawTransactionRecord,
    SourceRowRef,
    build_parse_result,
)

from .values import (
    LIANJIA_SOURCE,
    extract_layout,
    normalize_cell,
    normalize_key,
    parse_area_sqm,
    parse_days,
    parse_deal_date,
    parse_floor_bucket,
    parse_unit_price_cny_sqm,
    parse_wan_to_cny,
    parse_year,
)

LIANJIA_TRANSACTION_PARSER_VERSION = "lianjia-transaction-v1"

SOURCE_ID_FIELD = "链家编号"

_ALIASES: dict[str, tuple[str, ...]] = {
    SOURCE_ID_FIELD: ("链家编号", "房源编号", "成交编号"),
    "成交日期": ("成交日期", "成交时间", "签约日期"),
    "建筑面积": ("建筑面积", "面积"),
    "房屋户型": ("房屋户型", "户型"),
    "标题": ("标题", "房源标题", "title", "name"),
    "房屋朝向": ("房屋朝向", "朝向"),
    "所在楼层": ("所在楼层", "楼层"),
    "建成年代": ("建成年代", "建筑年代", "建成年份"),
    "挂牌价格（万）": ("挂牌价格（万）", "挂牌价格", "挂牌价", "挂牌总价"),
    "总价": ("总价", "成交价格（万）", "成交总价", "成交价"),
    "单价": ("单价", "成交单价", "单价（元/平米）"),
    "成交周期（天）": ("成交周期（天）", "成交周期", "成交用时"),
    "source_url": ("source_url", "房源链接", "链接", "url"),
    "raw_payload_ref": ("raw_payload_ref",),
}

_LOOKUP: dict[str, tuple[str, ...]] = {
    canonical: tuple(normalize_key(alias) for alias in aliases)
    for canonical, aliases in _ALIASES.items()
}


class LianjiaParseContext(FrozenModel):
    """Provenance the source rows cannot supply themselves.

    ``source_url``/``raw_payload_ref`` are batch-level defaults; a row may
    override them with its own values when the extraction layer captured them
    per row.
    """

    collected_at: AwareDatetime
    source_url: str | None = None
    raw_payload_ref: str | None = None


def parse_transaction_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    context: LianjiaParseContext,
) -> ParseResult:
    """Parse a batch of extracted Lianjia rows.

    One unintelligible row never discards its siblings: it becomes a
    :class:`ParseRejection` in the same result.
    """
    return build_parse_result(
        parse_transaction_row(row, context=context, row_index=index)
        for index, row in enumerate(rows)
    )


def parse_transaction_row(
    row: Mapping[str, Any],
    *,
    context: LianjiaParseContext,
    row_index: int | None = None,
) -> RawTransactionRecord | ParseRejection:
    """Parse one extracted Lianjia row into a provider DTO or a rejection."""
    if not isinstance(row, Mapping):
        return _reject(
            SourceRowRef(source=LIANJIA_SOURCE, row_index=row_index),
            ParseRejectionReason.MALFORMED_ROW,
            f"expected a field mapping, got {type(row).__name__}",
        )

    cells = _normalize_row_keys(row)
    source_url = _raw_text(cells, "source_url") or context.source_url
    raw_payload_ref = _raw_text(cells, "raw_payload_ref") or context.raw_payload_ref
    source_row_id = _raw_text(cells, SOURCE_ID_FIELD)
    row_ref = SourceRowRef(
        source=LIANJIA_SOURCE,
        row_index=row_index,
        source_row_id=source_row_id,
        source_url=source_url,
        raw_payload_ref=raw_payload_ref,
    )

    try:
        source_transaction_id = _cell(cells, SOURCE_ID_FIELD)
    except FieldParseError as error:
        return _reject(row_ref, error.reason, str(error), field=SOURCE_ID_FIELD)

    if source_transaction_id is None:
        # Provider-native identity is never fabricated: without 链家编号 the row
        # cannot be deduplicated or refreshed, so it stays visibly rejected.
        return _reject(
            row_ref,
            ParseRejectionReason.MISSING_SOURCE_IDENTITY,
            f"{SOURCE_ID_FIELD} is missing or empty",
            field=SOURCE_ID_FIELD,
        )

    try:
        fields = dict(
            deal_date=_convert(cells, "成交日期", parse_deal_date),
            area_sqm=_convert(cells, "建筑面积", parse_area_sqm),
            layout=_layout(cells),
            floor_bucket=_floor_bucket(cells),
            orientation=_cell(cells, "房屋朝向"),
            built_year=_convert(cells, "建成年代", parse_year),
            initial_listing_price_cny=_convert(cells, "挂牌价格（万）", parse_wan_to_cny),
            deal_price_cny=_convert(cells, "总价", parse_wan_to_cny),
            unit_price_cny_sqm=_convert(cells, "单价", parse_unit_price_cny_sqm),
            days_on_market=_convert(cells, "成交周期（天）", parse_days),
        )
    except FieldParseError as error:
        return _reject(row_ref, error.reason, str(error), field=error.field)

    try:
        return RawTransactionRecord(
            source=LIANJIA_SOURCE,
            source_transaction_id=source_transaction_id,
            source_url=source_url,
            raw_payload_ref=raw_payload_ref,
            collected_at=context.collected_at,
            parser_version=LIANJIA_TRANSACTION_PARSER_VERSION,
            **fields,
        )
    except ValidationError as error:
        return _reject(
            row_ref,
            ParseRejectionReason.SCHEMA_INVALID,
            _format_validation_error(error),
        )


def _normalize_row_keys(row: Mapping[str, Any]) -> dict[str, Any]:
    return {normalize_key(key): value for key, value in row.items()}


def _raw_value(cells: Mapping[str, Any], canonical: str) -> Any:
    for key in _LOOKUP[canonical]:
        if key in cells:
            return cells[key]
    return None


def _raw_text(cells: Mapping[str, Any], canonical: str) -> str | None:
    """Best-effort text used for provenance; never a reason to reject a row."""
    try:
        return _cell(cells, canonical)
    except FieldParseError:
        return None


def _cell(cells: Mapping[str, Any], canonical: str) -> str | None:
    try:
        return normalize_cell(_raw_value(cells, canonical))
    except FieldParseError as error:
        raise FieldParseError(str(error), field=canonical, reason=error.reason) from error


def _convert[T](
    cells: Mapping[str, Any],
    canonical: str,
    converter: Callable[..., T],
) -> T | None:
    text = _cell(cells, canonical)
    if text is None:
        return None
    return converter(text, field=canonical)


def _layout(cells: Mapping[str, Any]) -> str | None:
    """Prefer the dedicated layout field, fall back to the deal title."""
    layout = _cell(cells, "房屋户型")
    if layout is not None:
        return layout
    title = _cell(cells, "标题")
    return None if title is None else extract_layout(title)


def _floor_bucket(cells: Mapping[str, Any]) -> str | None:
    text = _cell(cells, "所在楼层")
    return None if text is None else parse_floor_bucket(text).value


def _reject(
    row: SourceRowRef,
    reason: ParseRejectionReason,
    detail: str,
    *,
    field: str | None = None,
) -> ParseRejection:
    return ParseRejection(row=row, reason=reason, field=field, detail=detail)


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
