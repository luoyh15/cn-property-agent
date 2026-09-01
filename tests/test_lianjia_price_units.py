from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cn_property_agent.providers import FieldParseError, ParseRejection, ParseRejectionReason
from cn_property_agent.providers.lianjia import LianjiaParseContext, parse_transaction_row
from cn_property_agent.providers.lianjia.values import parse_wan_to_cny


def test_wan_total_price_converts_to_cny() -> None:
    assert parse_wan_to_cny("1140") == pytest.approx(11_400_000.0)
    assert parse_wan_to_cny("1140万") == pytest.approx(11_400_000.0)
    assert parse_wan_to_cny("1140万元") == pytest.approx(11_400_000.0)


def test_wan_total_price_rejects_explicit_yuan_units() -> None:
    with pytest.raises(FieldParseError):
        parse_wan_to_cny("11400000元", field="总价")


def test_explicit_yuan_total_price_becomes_parse_rejection() -> None:
    result = parse_transaction_row(
        {"链家编号": "SH-UNIT-001", "总价": "11400000元"},
        context=LianjiaParseContext(collected_at=datetime(2026, 9, 1, tzinfo=UTC)),
    )

    assert isinstance(result, ParseRejection)
    assert result.reason is ParseRejectionReason.MALFORMED_FIELD
    assert result.field == "总价"
