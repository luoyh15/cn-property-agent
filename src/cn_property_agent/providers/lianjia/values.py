"""Scalar converters for Lianjia-style source values.

Every function here is pure and deterministic. Values that cannot be
interpreted raise :class:`FieldParseError` so the row parser can isolate the
row; values the source itself marks as unknown resolve to ``None``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from cn_property_agent.domain import FloorBucket
from cn_property_agent.providers import FieldParseError

CNY_PER_WAN = 10_000.0
"""A 万 is ten thousand CNY; Lianjia quotes total prices in 万元."""

UNKNOWN_MARKERS = frozenset(
    {"", "-", "--", "—", "暂无数据", "暂无", "未知", "无", "null", "none", "nan", "n/a"}
)
"""Values the source uses to say "not published", which are not parse failures."""

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")

# NFKC rewrites ``㎡`` to ``m2`` and ``²`` to ``2``, so unit tokens must be
# stripped before any digit is extracted. Longer tokens first.
_AREA_UNITS = ("平方米", "平方公尺", "平米", "平方", "m2", "㎡", "平")
_WAN_UNITS = ("万元", "万", "元", "人民币", "rmb", "cny", "¥", "￥")
_UNIT_PRICE_UNITS = ("元/平方米", "元/平米", "元/m2", "元/㎡", "元每平米", "元", "/") + _AREA_UNITS
_DAY_UNITS = ("天", "日", "days", "day")
_YEAR_UNITS = ("年建成", "年建", "年代", "年份", "年")

_DATE_PATTERNS = (
    re.compile(r"^(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})$"),
    re.compile(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日?$"),
)

_LAYOUT = re.compile(r"\d+室\d+厅|\d+房\d+厅|\d+室")

_FLOOR_BUCKETS: tuple[tuple[tuple[str, ...], FloorBucket], ...] = (
    (("低楼层", "低区"), FloorBucket.LOW),
    (("中楼层", "中区"), FloorBucket.MID),
    (("高楼层", "高区"), FloorBucket.HIGH),
)


def normalize_cell(value: object) -> str | None:
    """Normalize one raw cell to comparable text, or ``None`` when unknown.

    Applies NFKC so full-width digits/punctuation from the source collapse onto
    their ASCII forms, and treats the source's own "no data" markers as absent
    rather than malformed.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise FieldParseError(f"expected a scalar value, got {value!r}")
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    text = unicodedata.normalize("NFKC", text).strip()
    text = text.replace(",", "").replace("　", " ")
    if text.casefold() in UNKNOWN_MARKERS:
        return None
    return text


def normalize_key(key: object) -> str:
    """Normalize a source field name so half/full-width variants collide."""
    return "".join(unicodedata.normalize("NFKC", str(key)).split()).casefold()


def parse_number(text: str, *, units: tuple[str, ...] = (), field: str | None = None) -> float:
    """Parse exactly one number, allowing only the given unit tokens around it."""
    residual = text.casefold()
    for unit in units:
        residual = residual.replace(unit.casefold(), " ")
    matches = _NUMBER.findall(residual)
    if len(matches) != 1:
        raise FieldParseError(
            f"expected exactly one number in {text!r}, found {len(matches)}", field=field
        )
    leftover = _NUMBER.sub(" ", residual).strip()
    if leftover:
        raise FieldParseError(
            f"unexpected text {leftover!r} around the number in {text!r}", field=field
        )
    return float(matches[0])


def parse_int(text: str, *, units: tuple[str, ...] = (), field: str | None = None) -> int:
    value = parse_number(text, units=units, field=field)
    if not value.is_integer():
        raise FieldParseError(f"expected a whole number, got {text!r}", field=field)
    return int(value)


def parse_area_sqm(text: str, *, field: str | None = None) -> float:
    """``"120.5平米"`` / ``"120.5㎡"`` / ``"120.5"`` → square metres."""
    return parse_number(text, units=_AREA_UNITS, field=field)


def parse_wan_to_cny(text: str, *, field: str | None = None) -> float:
    """``"1140"`` / ``"1140万"`` (万元) → CNY."""
    return parse_number(text, units=_WAN_UNITS, field=field) * CNY_PER_WAN


def parse_unit_price_cny_sqm(text: str, *, field: str | None = None) -> float:
    """``"94606"`` / ``"94606元/平米"`` (元/㎡) → CNY per square metre."""
    return parse_number(text, units=_UNIT_PRICE_UNITS, field=field)


def parse_days(text: str, *, field: str | None = None) -> int:
    return parse_int(text, units=_DAY_UNITS, field=field)


def parse_year(text: str, *, field: str | None = None) -> int:
    return parse_int(text, units=_YEAR_UNITS, field=field)


def parse_deal_date(text: str, *, field: str | None = None) -> date:
    """Accept the full calendar dates Lianjia publishes; refuse partial ones."""
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        try:
            return date(
                int(match["year"]),
                int(match["month"]),
                int(match["day"]),
            )
        except ValueError as error:
            raise FieldParseError(f"{text!r} is not a calendar date: {error}", field=field) from error
    raise FieldParseError(f"unrecognized date format {text!r}", field=field)


def parse_floor_bucket(text: str) -> FloorBucket:
    """Map floor text to a bucket, conservatively.

    Only the source's explicit 低楼层/中楼层/高楼层 (or 低区/中区/高区) wording is
    bucketed. Anything else — ``顶层``, ``地下室``, a bare storey number, or a
    value mixing several bucket words — stays ``unknown`` rather than being
    guessed into a bucket that analytics would treat as observed. Bare ``高层``
    is not accepted either: in Lianjia text it usually describes the building
    type, not the unit's position in it.
    """
    matched = {bucket for tokens, bucket in _FLOOR_BUCKETS if any(token in text for token in tokens)}
    if len(matched) == 1:
        return matched.pop()
    return FloorBucket.UNKNOWN


def extract_layout(text: str) -> str | None:
    """Pull a ``N室M厅`` layout out of a title/name string."""
    match = _LAYOUT.search(text)
    return match.group(0) if match is not None else None
