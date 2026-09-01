from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest

from cn_property_agent.domain import Community, FloorBucket
from cn_property_agent.providers import ParseRejectionReason, TransactionProvider
from cn_property_agent.providers.lianjia import (
    LIANJIA_SOURCE,
    LIANJIA_TRANSACTION_PARSER_VERSION,
    LianjiaSnapshotError,
    RecordedLianjiaTransactionProvider,
)

SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "lianjia_transaction_snapshot.json"


@pytest.fixture
def snapshot_payload() -> dict[str, Any]:
    return json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def provider() -> RecordedLianjiaTransactionProvider:
    return RecordedLianjiaTransactionProvider(SNAPSHOT_FIXTURE)


def write_snapshot(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_provider_satisfies_the_transaction_provider_protocol(
    provider: RecordedLianjiaTransactionProvider,
) -> None:
    assert isinstance(provider, TransactionProvider)


@pytest.mark.asyncio
async def test_recorded_snapshot_yields_parsed_records(
    provider: RecordedLianjiaTransactionProvider,
    lianjia_community: Community,
) -> None:
    result = await provider.fetch_transactions(lianjia_community)

    assert (result.source_row_count, result.parsed_count) == (3, 2)
    assert [record.source_transaction_id for record in result.records] == [
        "SH1234567890",
        "SH1234567891",
    ]

    record = result.records[0]
    assert record.source == LIANJIA_SOURCE
    assert record.deal_date == date(2026, 7, 15)
    assert record.area_sqm == pytest.approx(120.5)
    assert record.layout == "3室2厅"
    assert record.floor_bucket == FloorBucket.MID.value
    assert record.built_year == 2008
    assert record.deal_price_cny == pytest.approx(11_400_000.0)
    assert record.unit_price_cny_sqm == pytest.approx(94_606.0)
    assert record.days_on_market == 41


@pytest.mark.asyncio
async def test_one_malformed_row_leaves_its_siblings_intact(
    provider: RecordedLianjiaTransactionProvider,
    lianjia_community: Community,
) -> None:
    result = await provider.fetch_transactions(lianjia_community)

    # Three recorded rows, one of which the parser refused: the refusal is
    # counted and described, not silently dropped.
    assert (result.source_row_count, result.parsed_count, result.parse_rejection_count) == (3, 2, 1)
    rejection = result.parse_rejections[0]
    assert rejection.reason is ParseRejectionReason.MALFORMED_FIELD
    assert rejection.field == "建筑面积"
    assert rejection.row.source == LIANJIA_SOURCE
    assert rejection.row.source_row_id == "SH1234567894"
    assert rejection.row.row_index == 2


@pytest.mark.asyncio
async def test_batch_provenance_reaches_parsed_records(
    provider: RecordedLianjiaTransactionProvider,
    lianjia_community: Community,
    snapshot_payload: dict[str, Any],
) -> None:
    result = await provider.fetch_transactions(lianjia_community)

    overridden, inherited = result.records
    assert overridden.source_url == "https://example.invalid/lianjia/chengjiao/SH1234567890.html"
    assert overridden.raw_payload_ref == "fixture://lianjia/chengjiao/SH1234567890"
    assert inherited.source_url == snapshot_payload["source_url"]
    assert inherited.raw_payload_ref == snapshot_payload["raw_payload_ref"]
    for record in result.records:
        assert record.collected_at.isoformat() == "2026-08-01T00:00:00+00:00"
        assert record.parser_version == LIANJIA_TRANSACTION_PARSER_VERSION
    # The rejected row keeps the same batch provenance pointer.
    assert result.parse_rejections[0].row.raw_payload_ref == snapshot_payload["raw_payload_ref"]


@pytest.mark.asyncio
async def test_empty_rows_is_a_successful_empty_fetch(
    tmp_path: Path,
    lianjia_community: Community,
    snapshot_payload: dict[str, Any],
) -> None:
    path = write_snapshot(tmp_path, {**snapshot_payload, "rows": []})

    result = await RecordedLianjiaTransactionProvider(path).fetch_transactions(lianjia_community)

    assert result.source_row_count == 0
    assert (result.records, result.parse_rejections) == ((), ())


@pytest.mark.asyncio
async def test_date_arguments_are_accepted_without_filtering(
    provider: RecordedLianjiaTransactionProvider,
    lianjia_community: Community,
) -> None:
    # Windowing is not this adapter's job; the snapshot is replayed whole.
    windowed = await provider.fetch_transactions(
        lianjia_community,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
    )

    assert windowed == await provider.fetch_transactions(lianjia_community)


@pytest.mark.asyncio
async def test_missing_file_raises_instead_of_returning_nothing(
    tmp_path: Path,
    lianjia_community: Community,
) -> None:
    provider = RecordedLianjiaTransactionProvider(tmp_path / "absent.json")

    with pytest.raises(LianjiaSnapshotError):
        await provider.fetch_transactions(lianjia_community)


@pytest.mark.asyncio
async def test_unreadable_json_raises(
    tmp_path: Path,
    lianjia_community: Community,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(LianjiaSnapshotError):
        await RecordedLianjiaTransactionProvider(path).fetch_transactions(lianjia_community)


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("top_level_array", lambda payload: payload["rows"]),
        ("rows_missing", lambda payload: {k: v for k, v in payload.items() if k != "rows"}),
        ("rows_not_an_array", lambda payload: {**payload, "rows": {"0": payload["rows"][0]}}),
        ("rows_is_a_string", lambda payload: {**payload, "rows": "[]"}),
        (
            "collected_at_missing",
            lambda payload: {k: v for k, v in payload.items() if k != "collected_at"},
        ),
        ("collected_at_naive", lambda payload: {**payload, "collected_at": "2026-08-01T00:00:00"}),
        ("collected_at_malformed", lambda payload: {**payload, "collected_at": "not-a-timestamp"}),
        ("source_url_blank", lambda payload: {**payload, "source_url": ""}),
    ],
)
@pytest.mark.asyncio
async def test_invalid_snapshot_never_looks_like_an_empty_success(
    tmp_path: Path,
    lianjia_community: Community,
    snapshot_payload: dict[str, Any],
    case: str,
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    path = write_snapshot(tmp_path, mutate(snapshot_payload))

    with pytest.raises(LianjiaSnapshotError) as error:
        await RecordedLianjiaTransactionProvider(path).fetch_transactions(lianjia_community)

    assert error.value.path == path
