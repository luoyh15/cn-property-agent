from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Callable

import pytest

from cn_property_agent.domain import Community, FloorBucket, ListingStatus
from cn_property_agent.providers import ListingProvider, ParseRejectionReason
from cn_property_agent.providers.lianjia import (
    LIANJIA_LISTING_PARSER_VERSION,
    LIANJIA_SOURCE,
    LianjiaSnapshotError,
    RecordedLianjiaListingProvider,
    build_listing_id,
)

SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "lianjia_listing_snapshot.json"


@pytest.fixture
def snapshot_payload() -> dict[str, Any]:
    return json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def provider() -> RecordedLianjiaListingProvider:
    return RecordedLianjiaListingProvider(SNAPSHOT_FIXTURE)


@pytest.fixture
def other_community(communities: list[Community], lianjia_community: Community) -> Community:
    return next(item for item in communities if item.community_id != lianjia_community.community_id)


def write_snapshot(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_provider_satisfies_the_listing_provider_protocol(
    provider: RecordedLianjiaListingProvider,
) -> None:
    assert isinstance(provider, ListingProvider)


@pytest.mark.asyncio
async def test_recorded_snapshot_yields_canonical_observations(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
) -> None:
    result = await provider.fetch_current_listings(lianjia_community)

    assert (result.source_row_count, result.parsed_count) == (3, 2)
    assert [item.listing.source_listing_id for item in result.observations] == [
        "SH107100000001",
        "SH107100000002",
    ]

    listing = result.observations[0].listing
    snapshot = result.observations[0].snapshot
    assert listing.source == LIANJIA_SOURCE
    assert listing.community_id == lianjia_community.community_id
    assert listing.area_sqm == pytest.approx(120.5)
    assert listing.layout == "3室2厅"
    assert listing.floor_bucket is FloorBucket.MID
    assert listing.built_year == 2008
    assert listing.status is ListingStatus.ACTIVE
    # 万元 → CNY; 单价 stays 元/㎡.
    assert snapshot.list_price_cny == pytest.approx(12_000_000.0)
    assert snapshot.unit_price_cny_sqm == pytest.approx(99_585.0)


@pytest.mark.asyncio
async def test_one_malformed_row_leaves_its_siblings_intact(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
) -> None:
    result = await provider.fetch_current_listings(lianjia_community)

    # Three recorded rows, one of which the parser refused: the refusal is
    # counted and described, not silently dropped.
    assert (result.source_row_count, result.parsed_count, result.parse_rejection_count) == (3, 2, 1)
    rejection = result.parse_rejections[0]
    assert rejection.reason is ParseRejectionReason.MALFORMED_FIELD
    assert rejection.field == "总价"
    assert rejection.row.source == LIANJIA_SOURCE
    assert rejection.row.source_row_id == "SH107100000007"
    assert rejection.row.row_index == 2


@pytest.mark.asyncio
async def test_listing_identity_is_the_parsers_stable_identity(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
) -> None:
    result = await provider.fetch_current_listings(lianjia_community)

    observation = result.observations[0]
    expected = build_listing_id(LIANJIA_SOURCE, "SH107100000001")
    assert observation.listing.listing_id == expected
    assert observation.snapshot.listing_id == expected


@pytest.mark.asyncio
async def test_batch_provenance_reaches_parsed_observations(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
    snapshot_payload: dict[str, Any],
) -> None:
    result = await provider.fetch_current_listings(lianjia_community)

    overridden, inherited = (item.snapshot for item in result.observations)
    assert overridden.source_url == "https://example.invalid/lianjia/ershoufang/SH107100000001.html"
    assert overridden.raw_payload_ref == "fixture://lianjia/ershoufang/SH107100000001"
    assert inherited.source_url == snapshot_payload["source_url"]
    assert inherited.raw_payload_ref == snapshot_payload["raw_payload_ref"]
    for observation in result.observations:
        # One snapshot proves one instant: the batch time is both seen-bounds.
        assert observation.snapshot.snapshot_at.isoformat() == "2026-08-01T00:00:00+00:00"
        assert observation.listing.first_seen_at == observation.snapshot.snapshot_at
        assert observation.listing.last_seen_at == observation.snapshot.snapshot_at
        assert observation.snapshot.parser_version == LIANJIA_LISTING_PARSER_VERSION
    # The rejected row keeps the same batch provenance pointer.
    assert result.parse_rejections[0].row.raw_payload_ref == snapshot_payload["raw_payload_ref"]


@pytest.mark.asyncio
async def test_empty_rows_is_a_successful_empty_fetch(
    tmp_path: Path,
    lianjia_community: Community,
    snapshot_payload: dict[str, Any],
) -> None:
    path = write_snapshot(tmp_path, {**snapshot_payload, "rows": []})

    result = await RecordedLianjiaListingProvider(path).fetch_current_listings(lianjia_community)

    assert result.source_row_count == 0
    assert (result.observations, result.parse_rejections) == ((), ())


@pytest.mark.asyncio
async def test_replaying_the_same_snapshot_is_deterministic(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
) -> None:
    assert await provider.fetch_current_listings(
        lianjia_community
    ) == await provider.fetch_current_listings(lianjia_community)


@pytest.mark.asyncio
async def test_snapshot_recorded_for_another_community_is_refused(
    provider: RecordedLianjiaListingProvider,
    other_community: Community,
) -> None:
    # Answering with another community's listings would silently corrupt the
    # caller's evidence, so the mismatch is an error rather than a result.
    with pytest.raises(LianjiaSnapshotError, match="not the requested"):
        await provider.fetch_current_listings(other_community)


@pytest.mark.asyncio
async def test_missing_file_raises_instead_of_returning_nothing(
    tmp_path: Path,
    lianjia_community: Community,
) -> None:
    provider = RecordedLianjiaListingProvider(tmp_path / "absent.json")

    with pytest.raises(LianjiaSnapshotError):
        await provider.fetch_current_listings(lianjia_community)


@pytest.mark.asyncio
async def test_unreadable_json_raises(
    tmp_path: Path,
    lianjia_community: Community,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(LianjiaSnapshotError):
        await RecordedLianjiaListingProvider(path).fetch_current_listings(lianjia_community)


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("top_level_array", lambda payload: payload["rows"]),
        ("rows_missing", lambda payload: {k: v for k, v in payload.items() if k != "rows"}),
        ("rows_not_an_array", lambda payload: {**payload, "rows": {"0": payload["rows"][0]}}),
        ("rows_is_a_string", lambda payload: {**payload, "rows": "[]"}),
        (
            "community_id_missing",
            lambda payload: {k: v for k, v in payload.items() if k != "community_id"},
        ),
        ("community_id_blank", lambda payload: {**payload, "community_id": ""}),
        (
            "snapshot_at_missing",
            lambda payload: {k: v for k, v in payload.items() if k != "snapshot_at"},
        ),
        ("snapshot_at_naive", lambda payload: {**payload, "snapshot_at": "2026-08-01T00:00:00"}),
        ("snapshot_at_malformed", lambda payload: {**payload, "snapshot_at": "not-a-timestamp"}),
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
        await RecordedLianjiaListingProvider(path).fetch_current_listings(lianjia_community)

    assert error.value.path == path


@pytest.mark.asyncio
async def test_provider_reads_the_file_and_never_the_network(
    provider: RecordedLianjiaListingProvider,
    lianjia_community: Community,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a recorded provider must not open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    result = await provider.fetch_current_listings(lianjia_community)

    assert result.parsed_count == 2
