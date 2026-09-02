"""Lianjia current-listing field parser.

This module owns *all* Lianjia-specific interpretation of current-listing
fields: Chinese field names, 万元/㎡ units, floor and listing-status wording. It
consumes an already extracted mapping (whatever the transport/extraction layer
produced) plus provenance context, and emits canonical
:class:`~cn_property_agent.domain.ListingObservation` values.

It performs no I/O: no HTTP, no HTML parsing, no clock access, no file access.
The snapshot timestamp and the resolved community are supplied by the caller
through :class:`LianjiaListingParseContext`.

Why canonical models here and a DTO on the transaction path: a listing
observation needs no cross-source reconciliation before it is meaningful. Its
identity is fully determined by ``source`` + ``source_listing_id`` and its
measurements are what one snapshot saw, so the parser can emit the canonical
pair directly. Community identity is the one thing it cannot know, so the
caller resolves the community first and passes ``community_id`` in the context;
the parser never guesses a community from listing text.

Single-observation semantics: one row is exactly one observation. The parser
sets ``first_seen_at == last_seen_at == snapshot_at`` because that instant is
all the row proves. Real first/last-seen history is reconstructed downstream by
folding the stored ``listing_snapshot`` series — never by this parser inventing
a range it did not observe.

Failure boundary: this parser rejects a row only when a source value cannot be
interpreted at all, when provider-native identity is missing, or when the row
lacks the asking price a snapshot is defined by. Plausibility beyond the
canonical model constraints — market-level sanity, duplicate detection,
community-resolution confidence — belongs to the data-quality gates in the
service layer.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from pydantic import AwareDatetime, Field, ValidationError

from cn_property_agent.domain import (
    FloorBucket,
    FrozenModel,
    Listing,
    ListingObservation,
    ListingSnapshot,
    ListingStatus,
)
from cn_property_agent.providers import (
    FieldParseError,
    ListingParseResult,
    ParseRejection,
    ParseRejectionReason,
    SourceRowRef,
    build_listing_parse_result,
)
from cn_property_agent.utils import stable_id

from .values import (
    LIANJIA_SOURCE,
    extract_layout,
    normalize_cell,
    normalize_key,
    parse_area_sqm,
    parse_aware_datetime,
    parse_floor_bucket,
    parse_listing_status,
    parse_unit_price_cny_sqm,
    parse_wan_to_cny,
    parse_year,
)

LIANJIA_LISTING_PARSER_VERSION = "lianjia-listing-v1"

SOURCE_ID_FIELD = "房源编号"
LIST_PRICE_FIELD = "总价"
SNAPSHOT_AT_FIELD = "快照时间"

_ALIASES: dict[str, tuple[str, ...]] = {
    SOURCE_ID_FIELD: ("房源编号", "链家编号", "房源id", "source_listing_id"),
    LIST_PRICE_FIELD: ("总价", "挂牌价格（万）", "挂牌价格", "挂牌价", "售价", "报价"),
    "单价": ("单价", "挂牌单价", "单价（元/平米）", "参考单价"),
    "建筑面积": ("建筑面积", "面积"),
    "房屋户型": ("房屋户型", "户型"),
    "标题": ("标题", "房源标题", "title", "name"),
    "房屋朝向": ("房屋朝向", "朝向"),
    "所在楼层": ("所在楼层", "楼层"),
    "建成年代": ("建成年代", "建筑年代", "建成年份"),
    "建筑类型": ("建筑类型", "楼型"),
    "挂牌状态": ("挂牌状态", "房源状态", "状态", "status"),
    SNAPSHOT_AT_FIELD: ("快照时间", "采集时间", "snapshot_at"),
    "source_url": ("source_url", "房源链接", "链接", "url"),
    "raw_payload_ref": ("raw_payload_ref",),
}

_LOOKUP: dict[str, tuple[str, ...]] = {
    canonical: tuple(normalize_key(alias) for alias in aliases)
    for canonical, aliases in _ALIASES.items()
}


class LianjiaListingParseContext(FrozenModel):
    """What the source rows cannot supply themselves.

    ``community_id`` is the already resolved community the snapshot was
    captured for; identity resolution stays with the caller. ``snapshot_at`` is
    when the batch was observed. ``source_url``/``raw_payload_ref`` are
    batch-level defaults; a row may override them — and ``snapshot_at`` — with
    its own values when the extraction layer captured them per row.
    """

    community_id: str = Field(min_length=1)
    snapshot_at: AwareDatetime
    source_url: str | None = None
    raw_payload_ref: str | None = None


def build_listing_id(source: str, source_listing_id: str) -> str:
    """Derive the stable internal listing id from provider-native identity.

    Mirrors ``build_transaction_id``: the id depends only on the source and the
    source's own listing id, never on price, status or snapshot time. The same
    source listing therefore keeps one identity across every snapshot, which is
    what lets a price history be assembled from separate observations.
    """
    return stable_id("lst", source, source_listing_id)


def parse_listing_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    context: LianjiaListingParseContext,
) -> ListingParseResult:
    """Parse a batch of extracted Lianjia listing rows.

    One unintelligible row never discards its siblings: it becomes a
    :class:`ParseRejection` in the same result. An empty batch is a successful
    empty result, not a failure.
    """
    return build_listing_parse_result(
        parse_listing_row(row, context=context, row_index=index)
        for index, row in enumerate(rows)
    )


def parse_listing_row(
    row: Mapping[str, Any],
    *,
    context: LianjiaListingParseContext,
    row_index: int | None = None,
) -> ListingObservation | ParseRejection:
    """Parse one extracted Lianjia listing row into an observation or a rejection."""
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
        source_listing_id = _cell(cells, SOURCE_ID_FIELD)
    except FieldParseError as error:
        return _reject(row_ref, error.reason, str(error), field=SOURCE_ID_FIELD)

    if source_listing_id is None:
        # Provider-native identity is never fabricated: without 房源编号 the row
        # cannot be tracked across snapshots, so it stays visibly rejected.
        return _reject(
            row_ref,
            ParseRejectionReason.MISSING_SOURCE_IDENTITY,
            f"{SOURCE_ID_FIELD} is missing or empty",
            field=SOURCE_ID_FIELD,
        )

    try:
        snapshot_at = _convert(cells, SNAPSHOT_AT_FIELD, parse_aware_datetime)
        list_price_cny = _convert(cells, LIST_PRICE_FIELD, parse_wan_to_cny)
        unit_price_cny_sqm = _convert(cells, "单价", parse_unit_price_cny_sqm)
        area_sqm = _convert(cells, "建筑面积", parse_area_sqm)
        built_year = _convert(cells, "建成年代", parse_year)
        orientation = _cell(cells, "房屋朝向")
        building_type = _cell(cells, "建筑类型")
        layout = _layout(cells)
        floor_bucket = _floor_bucket(cells)
        status = _status(cells)
    except FieldParseError as error:
        return _reject(row_ref, error.reason, str(error), field=error.field)

    if list_price_cny is None:
        # A snapshot without an asking price observes nothing about price; it
        # is reported rather than stored as a priceless listing state.
        return _reject(
            row_ref,
            ParseRejectionReason.SCHEMA_INVALID,
            f"{LIST_PRICE_FIELD} is required: a listing snapshot records an asking price",
            field=LIST_PRICE_FIELD,
        )

    observed_at = snapshot_at or context.snapshot_at
    listing_id = build_listing_id(LIANJIA_SOURCE, source_listing_id)

    try:
        return ListingObservation(
            listing=Listing(
                listing_id=listing_id,
                community_id=context.community_id,
                source=LIANJIA_SOURCE,
                source_listing_id=source_listing_id,
                area_sqm=area_sqm,
                layout=layout,
                floor_bucket=floor_bucket,
                orientation=orientation,
                built_year=built_year,
                building_type=building_type,
                # One row proves one instant; see the module docstring.
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                status=status,
            ),
            snapshot=ListingSnapshot(
                listing_id=listing_id,
                snapshot_at=observed_at,
                list_price_cny=list_price_cny,
                unit_price_cny_sqm=unit_price_cny_sqm,
                status=status,
                source=LIANJIA_SOURCE,
                source_url=source_url,
                raw_payload_ref=raw_payload_ref,
                parser_version=LIANJIA_LISTING_PARSER_VERSION,
            ),
        )
    except ValidationError as error:
        return _reject(row_ref, ParseRejectionReason.SCHEMA_INVALID, _format_validation_error(error))


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
    """Prefer the dedicated layout field, fall back to the listing title."""
    layout = _cell(cells, "房屋户型")
    if layout is not None:
        return layout
    title = _cell(cells, "标题")
    return None if title is None else extract_layout(title)


def _floor_bucket(cells: Mapping[str, Any]) -> FloorBucket:
    text = _cell(cells, "所在楼层")
    return FloorBucket.UNKNOWN if text is None else parse_floor_bucket(text)


def _status(cells: Mapping[str, Any]) -> ListingStatus:
    text = _cell(cells, "挂牌状态")
    return ListingStatus.UNKNOWN if text is None else parse_listing_status(text)


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
