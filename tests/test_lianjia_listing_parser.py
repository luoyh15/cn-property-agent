from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cn_property_agent.domain import FloorBucket, ListingObservation, ListingStatus
from cn_property_agent.providers import ParseRejection, ParseRejectionReason
from cn_property_agent.providers.lianjia import (
    LIANJIA_LISTING_PARSER_VERSION,
    LIANJIA_SOURCE,
    LianjiaListingParseContext,
    build_listing_id,
    parse_listing_row,
    parse_listing_rows,
)
from cn_property_agent.providers.lianjia import listing_parser
from cn_property_agent.providers.lianjia.values import parse_listing_status


def parse_one(
    rows: dict[str, dict],
    name: str,
    context: LianjiaListingParseContext,
) -> ListingObservation | ParseRejection:
    return parse_listing_row(rows[name], context=context, row_index=0)


def test_valid_row_maps_every_supported_field(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    listing = observation.listing
    snapshot = observation.snapshot
    assert listing.source == LIANJIA_SOURCE
    assert listing.source_listing_id == "SH107100000001"
    assert listing.community_id == lianjia_listing_context.community_id
    assert listing.area_sqm == pytest.approx(120.5)
    assert listing.layout == "3室2厅"
    assert listing.floor_bucket is FloorBucket.MID
    assert listing.orientation == "南"
    assert listing.built_year == 2008
    assert listing.building_type == "板楼"
    assert listing.status is ListingStatus.ACTIVE
    # 万元 → CNY; 单价 stays 元/㎡.
    assert snapshot.list_price_cny == pytest.approx(12_000_000.0)
    assert snapshot.unit_price_cny_sqm == pytest.approx(99_585.0)
    assert snapshot.status is ListingStatus.ACTIVE


def test_units_and_field_name_variants_are_accepted(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(lianjia_listing_rows, "valid_title_fallback", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    assert observation.listing.area_sqm == pytest.approx(88.0)
    assert observation.listing.orientation == "东南"
    assert observation.listing.floor_bucket is FloorBucket.HIGH
    # 房屋户型 is absent: the layout comes from the listing title instead.
    assert "房屋户型" not in lianjia_listing_rows["valid_title_fallback"]
    assert observation.listing.layout == "2室1厅"
    assert observation.snapshot.list_price_cny == pytest.approx(8_300_000.0)
    assert observation.snapshot.unit_price_cny_sqm == pytest.approx(94_318.0)


def test_absent_optional_fields_stay_none(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(lianjia_listing_rows, "valid_minimal", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    assert observation.listing.area_sqm is None
    assert observation.listing.layout is None
    assert observation.listing.orientation is None
    assert observation.listing.built_year is None
    assert observation.listing.building_type is None
    assert observation.listing.floor_bucket is FloorBucket.UNKNOWN
    assert observation.listing.status is ListingStatus.UNKNOWN
    # No unit price is published and none is invented from price/area.
    assert observation.snapshot.unit_price_cny_sqm is None


def test_source_unknown_markers_become_absent_not_rejections(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(lianjia_listing_rows, "valid_unknown_markers", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    assert lianjia_listing_rows["valid_unknown_markers"]["建成年代"] == "暂无数据"
    assert observation.listing.built_year is None
    assert observation.listing.status is ListingStatus.UNKNOWN


def test_listing_id_is_stable_across_snapshot_contexts(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
    lianjia_later_listing_context: LianjiaListingParseContext,
) -> None:
    """The same source listing keeps one identity however often it is observed."""
    first = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)
    later = parse_one(lianjia_listing_rows, "valid_full", lianjia_later_listing_context)

    assert isinstance(first, ListingObservation)
    assert isinstance(later, ListingObservation)
    assert lianjia_listing_context.snapshot_at != lianjia_later_listing_context.snapshot_at
    assert first.listing.listing_id == later.listing.listing_id
    assert first.listing.listing_id == build_listing_id(LIANJIA_SOURCE, "SH107100000001")
    assert first.snapshot.listing_id == first.listing.listing_id


def test_listing_id_separates_distinct_source_listings(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    first = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)
    other = parse_one(lianjia_listing_rows, "valid_title_fallback", lianjia_listing_context)

    assert isinstance(first, ListingObservation)
    assert isinstance(other, ListingObservation)
    assert first.listing.listing_id != other.listing.listing_id


def test_price_and_status_change_does_not_change_identity(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
    lianjia_later_listing_context: LianjiaListingParseContext,
) -> None:
    before = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)
    after = parse_one(
        lianjia_listing_rows, "valid_full_after_price_cut", lianjia_later_listing_context
    )

    assert isinstance(before, ListingObservation)
    assert isinstance(after, ListingObservation)
    assert before.listing.listing_id == after.listing.listing_id
    assert after.snapshot.list_price_cny == pytest.approx(11_300_000.0)
    assert after.snapshot.list_price_cny < before.snapshot.list_price_cny
    assert before.snapshot.status is ListingStatus.ACTIVE
    assert after.snapshot.status is ListingStatus.WITHDRAWN
    assert after.snapshot.snapshot_at == lianjia_later_listing_context.snapshot_at


def test_one_row_is_one_observation_not_a_seen_range(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    """A single row proves one instant; history is never inferred from it."""
    observation = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    snapshot_at = lianjia_listing_context.snapshot_at
    assert observation.listing.first_seen_at == snapshot_at
    assert observation.listing.last_seen_at == snapshot_at
    assert observation.snapshot.snapshot_at == snapshot_at


def test_provenance_comes_from_context_and_row_overrides(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    overridden = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)
    inherited = parse_one(lianjia_listing_rows, "valid_title_fallback", lianjia_listing_context)

    assert isinstance(overridden, ListingObservation)
    assert isinstance(inherited, ListingObservation)
    assert overridden.snapshot.source_url == (
        "https://example.invalid/lianjia/ershoufang/SH107100000001.html"
    )
    assert overridden.snapshot.raw_payload_ref == "fixture://lianjia/ershoufang/SH107100000001"
    assert inherited.snapshot.source_url == lianjia_listing_context.source_url
    assert inherited.snapshot.raw_payload_ref == lianjia_listing_context.raw_payload_ref
    assert inherited.snapshot.source == LIANJIA_SOURCE
    assert inherited.snapshot.parser_version == LIANJIA_LISTING_PARSER_VERSION


def test_absent_provenance_is_not_invented() -> None:
    context = LianjiaListingParseContext(
        community_id="cm-sh-pd-002",
        snapshot_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    observation = parse_listing_row({"房源编号": "SH1", "总价": "600"}, context=context)

    assert isinstance(observation, ListingObservation)
    assert observation.snapshot.source_url is None
    assert observation.snapshot.raw_payload_ref is None


def test_row_snapshot_timestamp_overrides_the_batch_timestamp(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(lianjia_listing_rows, "valid_row_snapshot_at", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    expected = datetime(2026, 8, 2, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    assert observation.snapshot.snapshot_at == expected
    assert observation.listing.first_seen_at == expected
    assert observation.listing.last_seen_at == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("在售", ListingStatus.ACTIVE),
        ("出售中", ListingStatus.ACTIVE),
        ("已成交", ListingStatus.SOLD),
        ("已下架", ListingStatus.WITHDRAWN),
        ("已停售", ListingStatus.OFF_MARKET),
        ("暂不可售", ListingStatus.OFF_MARKET),
        ("可议价", ListingStatus.UNKNOWN),
        ("降价房源", ListingStatus.UNKNOWN),
        ("在售(已成交1套)", ListingStatus.UNKNOWN),
    ],
)
def test_status_mapping_is_conservative(text: str, expected: ListingStatus) -> None:
    assert parse_listing_status(text) is expected


def test_unrecognized_status_text_is_not_a_rejection(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    observation = parse_one(
        lianjia_listing_rows, "valid_unrecognized_status", lianjia_listing_context
    )

    assert isinstance(observation, ListingObservation)
    assert observation.listing.status is ListingStatus.UNKNOWN
    assert observation.listing.floor_bucket is FloorBucket.UNKNOWN


@pytest.mark.parametrize(
    ("name", "reason", "field"),
    [
        ("malformed_total_price", ParseRejectionReason.MALFORMED_FIELD, "总价"),
        ("malformed_area", ParseRejectionReason.MALFORMED_FIELD, "建筑面积"),
        ("malformed_snapshot_at", ParseRejectionReason.MALFORMED_FIELD, "快照时间"),
        ("naive_snapshot_at", ParseRejectionReason.MALFORMED_FIELD, "快照时间"),
        ("missing_total_price", ParseRejectionReason.SCHEMA_INVALID, "总价"),
        ("missing_source_id", ParseRejectionReason.MISSING_SOURCE_IDENTITY, "房源编号"),
        ("blank_source_id", ParseRejectionReason.MISSING_SOURCE_IDENTITY, "房源编号"),
    ],
)
def test_unusable_values_become_parse_rejections(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
    name: str,
    reason: ParseRejectionReason,
    field: str,
) -> None:
    rejection = parse_one(lianjia_listing_rows, name, lianjia_listing_context)

    assert isinstance(rejection, ParseRejection)
    assert rejection.reason is reason
    assert rejection.field == field
    assert rejection.detail


def test_non_positive_area_is_rejected_not_repaired(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    rejection = parse_one(lianjia_listing_rows, "non_positive_area", lianjia_listing_context)

    assert isinstance(rejection, ParseRejection)
    assert rejection.reason is ParseRejectionReason.SCHEMA_INVALID
    assert "area_sqm" in rejection.detail


def test_rejection_identifies_the_row_without_the_payload(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    rejection = parse_listing_row(
        lianjia_listing_rows["malformed_area"], context=lianjia_listing_context, row_index=7
    )

    assert isinstance(rejection, ParseRejection)
    assert rejection.row.source == LIANJIA_SOURCE
    assert rejection.row.row_index == 7
    assert rejection.row.source_row_id == "SH107100000008"
    assert rejection.row.source_url == lianjia_listing_context.source_url
    assert rejection.row.raw_payload_ref == lianjia_listing_context.raw_payload_ref
    assert "约九十平米" in rejection.detail


def test_missing_identity_is_visible_and_never_invented(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    rejection = parse_one(lianjia_listing_rows, "missing_source_id", lianjia_listing_context)

    assert isinstance(rejection, ParseRejection)
    assert rejection.row.source_row_id is None


def test_non_mapping_row_is_rejected_not_raised(
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    rejection = parse_listing_row(
        ["not", "a", "mapping"], context=lianjia_listing_context, row_index=3
    )

    assert isinstance(rejection, ParseRejection)
    assert rejection.reason is ParseRejectionReason.MALFORMED_ROW
    assert rejection.row.row_index == 3


def test_malformed_row_is_isolated_from_valid_rows(
    lianjia_listing_fixture: dict,
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    names = lianjia_listing_fixture["batches"]["one_valid_one_malformed"]
    batch = [lianjia_listing_rows[name] for name in names]

    result = parse_listing_rows(batch, context=lianjia_listing_context)

    assert (result.parsed_count, result.rejected_count) == (1, 1)
    assert result.observations[0].listing.source_listing_id == "SH107100000001"
    assert result.rejections[0].row.row_index == 1
    assert result.rejections[0].field == "总价"


def test_empty_batch_is_a_successful_empty_result(
    lianjia_listing_fixture: dict,
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    result = parse_listing_rows(
        lianjia_listing_fixture["batches"]["empty"], context=lianjia_listing_context
    )

    assert result.observations == ()
    assert result.rejections == ()
    assert (result.parsed_count, result.rejected_count) == (0, 0)


def test_batch_parsing_is_deterministic(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    batch = list(lianjia_listing_rows.values())

    first = parse_listing_rows(batch, context=lianjia_listing_context)
    second = parse_listing_rows(batch, context=lianjia_listing_context)

    assert first == second
    assert first.parsed_count + first.rejected_count == len(batch)
    assert first.parsed_count > 0
    assert first.rejected_count > 0


def test_parser_performs_no_io() -> None:
    """The parser only interprets values it was handed: no I/O, no clock."""
    source = Path(listing_parser.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            imported.add(node.module)

    assert imported <= {
        "__future__",
        "typing",
        "pydantic",
        "cn_property_agent.domain",
        "cn_property_agent.providers",
        "cn_property_agent.utils",
    }


def test_parser_output_carries_no_source_specific_types(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> None:
    """Downstream consumers see canonical models only, plus a named source."""
    observation = parse_one(lianjia_listing_rows, "valid_full", lianjia_listing_context)

    assert isinstance(observation, ListingObservation)
    dumped = observation.model_dump()
    assert set(dumped) == {"listing", "snapshot"}
    assert dumped["listing"]["source"] == LIANJIA_SOURCE
    assert dumped["snapshot"]["parser_version"] == LIANJIA_LISTING_PARSER_VERSION
