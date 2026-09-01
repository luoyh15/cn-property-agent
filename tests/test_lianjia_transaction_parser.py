from __future__ import annotations

from datetime import date

import pytest

from cn_property_agent.domain import FloorBucket
from cn_property_agent.providers import (
    ParseRejection,
    ParseRejectionReason,
    RawTransactionRecord,
)
from cn_property_agent.providers.lianjia import (
    LIANJIA_SOURCE,
    LIANJIA_TRANSACTION_PARSER_VERSION,
    LianjiaParseContext,
    parse_transaction_row,
    parse_transaction_rows,
)
from cn_property_agent.providers.lianjia.values import parse_floor_bucket


def parse_one(
    rows: dict[str, dict],
    name: str,
    context: LianjiaParseContext,
) -> RawTransactionRecord | ParseRejection:
    return parse_transaction_row(rows[name], context=context, row_index=0)


def test_valid_row_maps_every_supported_field(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_full", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert record.source == LIANJIA_SOURCE
    assert record.source_transaction_id == "SH1234567890"
    assert record.deal_date == date(2026, 7, 15)
    assert record.area_sqm == pytest.approx(120.5)
    assert record.layout == "3室2厅"
    assert record.floor_bucket == FloorBucket.MID.value
    assert record.orientation == "南"
    assert record.built_year == 2008
    assert record.initial_listing_price_cny == pytest.approx(12_000_000.0)
    assert record.deal_price_cny == pytest.approx(11_400_000.0)
    assert record.unit_price_cny_sqm == pytest.approx(94_606.0)
    assert record.days_on_market == 41


def test_provenance_comes_from_context_and_row_overrides(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    overridden = parse_one(lianjia_rows, "valid_full", lianjia_context)
    inherited = parse_one(lianjia_rows, "valid_title_fallback", lianjia_context)

    assert isinstance(overridden, RawTransactionRecord)
    assert isinstance(inherited, RawTransactionRecord)
    assert overridden.source_url == "https://example.invalid/lianjia/chengjiao/SH1234567890.html"
    assert overridden.raw_payload_ref == "fixture://lianjia/chengjiao/SH1234567890"
    assert inherited.source_url == lianjia_context.source_url
    assert inherited.raw_payload_ref == lianjia_context.raw_payload_ref
    assert inherited.collected_at == lianjia_context.collected_at
    assert inherited.parser_version == LIANJIA_TRANSACTION_PARSER_VERSION


def test_units_are_converted_into_dto_conventions(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_title_fallback", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    # 万元 → CNY, ㎡ suffix stripped, 元/平米 kept as CNY per square metre.
    assert record.initial_listing_price_cny == pytest.approx(8_300_000.0)
    assert record.deal_price_cny == pytest.approx(7_920_000.0)
    assert record.area_sqm == pytest.approx(88.0)
    assert record.unit_price_cny_sqm == pytest.approx(90_000.0)
    assert record.days_on_market == 63
    assert record.deal_price_cny == pytest.approx(record.unit_price_cny_sqm * record.area_sqm)


def test_layout_falls_back_to_the_deal_title(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_title_fallback", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert "房屋户型" not in lianjia_rows["valid_title_fallback"]
    assert record.layout == "2室1厅"


def test_full_width_digits_and_separators_are_normalized(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_fullwidth_digits", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert record.source_transaction_id == "SH1234567892"
    assert record.deal_date == date(2026, 5, 20)
    assert record.area_sqm == pytest.approx(100.0)
    assert record.deal_price_cny == pytest.approx(9_000_000.0)
    assert record.unit_price_cny_sqm == pytest.approx(90_000.0)


def test_source_unknown_markers_become_absent_not_rejections(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_fullwidth_digits", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert lianjia_rows["valid_fullwidth_digits"]["建成年代"] == "暂无数据"
    assert record.built_year is None


def test_absent_optional_fields_stay_none(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_unknown_floor", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert record.orientation is None
    assert record.built_year is None
    assert record.initial_listing_price_cny is None
    assert record.days_on_market is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("低楼层(共6层)", FloorBucket.LOW),
        ("中楼层(共18层)", FloorBucket.MID),
        ("高楼层(共32层)", FloorBucket.HIGH),
        ("低区/6层", FloorBucket.LOW),
        ("顶层(共6层)", FloorBucket.UNKNOWN),
        ("底层(共6层)", FloorBucket.UNKNOWN),
        ("地下室", FloorBucket.UNKNOWN),
        ("第5层", FloorBucket.UNKNOWN),
        ("共18层", FloorBucket.UNKNOWN),
        ("高层建筑", FloorBucket.UNKNOWN),
        ("低楼层转高楼层", FloorBucket.UNKNOWN),
        ("未知", FloorBucket.UNKNOWN),
    ],
)
def test_floor_bucketing_is_conservative(text: str, expected: FloorBucket) -> None:
    assert parse_floor_bucket(text) is expected


def test_unrecognized_floor_text_is_not_a_rejection(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_one(lianjia_rows, "valid_unknown_floor", lianjia_context)

    assert isinstance(record, RawTransactionRecord)
    assert record.floor_bucket == FloorBucket.UNKNOWN.value


@pytest.mark.parametrize(
    ("name", "reason", "field"),
    [
        ("malformed_area", ParseRejectionReason.MALFORMED_FIELD, "建筑面积"),
        ("malformed_deal_date", ParseRejectionReason.MALFORMED_FIELD, "成交日期"),
        ("impossible_deal_date", ParseRejectionReason.MALFORMED_FIELD, "成交日期"),
        ("malformed_total_price", ParseRejectionReason.MALFORMED_FIELD, "总价"),
        ("missing_source_id", ParseRejectionReason.MISSING_SOURCE_IDENTITY, "链家编号"),
        ("blank_source_id", ParseRejectionReason.MISSING_SOURCE_IDENTITY, "链家编号"),
    ],
)
def test_malformed_values_become_parse_rejections(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
    name: str,
    reason: ParseRejectionReason,
    field: str,
) -> None:
    rejection = parse_one(lianjia_rows, name, lianjia_context)

    assert isinstance(rejection, ParseRejection)
    assert rejection.reason is reason
    assert rejection.field == field
    assert rejection.detail


def test_rejection_identifies_the_row_without_the_payload(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rejection = parse_transaction_row(
        lianjia_rows["malformed_area"], context=lianjia_context, row_index=7
    )

    assert isinstance(rejection, ParseRejection)
    assert rejection.row.source == LIANJIA_SOURCE
    assert rejection.row.row_index == 7
    assert rejection.row.source_row_id == "SH1234567894"
    assert rejection.row.source_url == lianjia_context.source_url
    assert rejection.row.raw_payload_ref == lianjia_context.raw_payload_ref
    # The offending value is quoted in `detail`, but the row payload itself is
    # not copied into the rejection: siblings of the bad field stay out of it.
    serialized = rejection.model_dump_json()
    assert "约一百二十平米" in rejection.detail
    assert set(rejection.row.model_dump()) == {
        "source",
        "row_index",
        "source_row_id",
        "source_url",
        "raw_payload_ref",
    }
    assert all(
        str(value) not in serialized
        for key, value in lianjia_rows["malformed_area"].items()
        if key not in ("链家编号", "建筑面积")
    )


def test_missing_identity_is_visible_and_never_invented(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    rejection = parse_one(lianjia_rows, "missing_source_id", lianjia_context)

    assert isinstance(rejection, ParseRejection)
    assert rejection.row.source_row_id is None


def test_non_mapping_row_is_rejected_not_raised(
    lianjia_context: LianjiaParseContext,
) -> None:
    rejection = parse_transaction_row(["not", "a", "mapping"], context=lianjia_context, row_index=3)

    assert isinstance(rejection, ParseRejection)
    assert rejection.reason is ParseRejectionReason.MALFORMED_ROW
    assert rejection.row.row_index == 3


def test_malformed_row_is_isolated_from_valid_rows(
    lianjia_fixture: dict,
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    batch = [lianjia_rows[name] for name in lianjia_fixture["batches"]["one_valid_one_malformed"]]

    result = parse_transaction_rows(batch, context=lianjia_context)

    assert (result.parsed_count, result.rejected_count) == (1, 1)
    assert result.records[0].source_transaction_id == "SH1234567890"
    assert result.rejections[0].row.row_index == 1
    assert result.rejections[0].field == "建筑面积"


def test_batch_parsing_is_deterministic(
    lianjia_rows: dict[str, dict],
    lianjia_context: LianjiaParseContext,
) -> None:
    batch = list(lianjia_rows.values())

    first = parse_transaction_rows(batch, context=lianjia_context)
    second = parse_transaction_rows(batch, context=lianjia_context)

    assert first == second
    assert first.parsed_count + first.rejected_count == len(batch)


def test_parser_accepts_field_name_variants(
    lianjia_context: LianjiaParseContext,
) -> None:
    record = parse_transaction_row(
        {
            "房源编号": "SH9999999999",
            "成交时间": "2026-04-01",
            "面积": "70平方米",
            "户型": "2室1厅",
            "朝向": "西",
            "楼层": "低楼层(共11层)",
            "成交总价": "560",
            "成交单价": "80000",
            "成交周期": "20",
        },
        context=lianjia_context,
    )

    assert isinstance(record, RawTransactionRecord)
    assert record.source_transaction_id == "SH9999999999"
    assert record.area_sqm == pytest.approx(70.0)
    assert record.deal_price_cny == pytest.approx(5_600_000.0)
    assert record.floor_bucket == FloorBucket.LOW.value
