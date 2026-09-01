from __future__ import annotations

import re
from pathlib import Path

import pytest

from cn_property_agent.config import (
    SNAPSHOT_PATH_ENV_VAR,
    CityProfile,
    CityProviderNames,
    ProviderConfigurationError,
    ProviderSettings,
    build_transaction_provider,
    load_city_profile,
)
from cn_property_agent.domain import Community
from cn_property_agent.providers import TransactionProvider
from cn_property_agent.providers.lianjia import RecordedLianjiaTransactionProvider

CITY_PROFILE_DIR = Path(__file__).parents[1] / "configs" / "cities"
SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "lianjia_transaction_snapshot.json"
PACKAGE_DIR = Path(__file__).parents[1] / "src" / "cn_property_agent"

# Layers that must stay source-independent: they may never name a source.
SOURCE_INDEPENDENT_PACKAGES = ("services", "analytics", "domain", "storage", "api", "mcp", "agent")


@pytest.fixture
def shanghai_profile() -> CityProfile:
    return load_city_profile("shanghai", directory=CITY_PROFILE_DIR)


@pytest.fixture
def recorded_settings() -> ProviderSettings:
    return ProviderSettings(lianjia_transaction_snapshot_path=SNAPSHOT_FIXTURE)


def test_shanghai_with_a_snapshot_path_builds_the_recorded_lianjia_provider(
    shanghai_profile: CityProfile,
    recorded_settings: ProviderSettings,
) -> None:
    provider = build_transaction_provider(shanghai_profile, recorded_settings)

    assert isinstance(provider, RecordedLianjiaTransactionProvider)
    assert isinstance(provider, TransactionProvider)
    assert provider.snapshot_path == SNAPSHOT_FIXTURE


@pytest.mark.asyncio
async def test_composed_provider_replays_the_configured_snapshot(
    shanghai_profile: CityProfile,
    recorded_settings: ProviderSettings,
    lianjia_community: Community,
) -> None:
    provider = build_transaction_provider(shanghai_profile, recorded_settings)

    result = await provider.fetch_transactions(lianjia_community)

    assert (result.source_row_count, result.parsed_count) == (3, 2)


def test_snapshot_path_is_read_from_the_environment(
    shanghai_profile: CityProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SNAPSHOT_PATH_ENV_VAR, str(SNAPSHOT_FIXTURE))

    provider = build_transaction_provider(shanghai_profile, ProviderSettings())

    assert isinstance(provider, RecordedLianjiaTransactionProvider)


def test_missing_snapshot_path_fails_instead_of_building_an_empty_provider(
    shanghai_profile: CityProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SNAPSHOT_PATH_ENV_VAR, raising=False)

    with pytest.raises(ProviderConfigurationError, match=SNAPSHOT_PATH_ENV_VAR):
        build_transaction_provider(shanghai_profile, ProviderSettings())


def test_snapshot_path_that_is_not_a_file_fails_at_composition_time(
    shanghai_profile: CityProfile,
    tmp_path: Path,
) -> None:
    settings = ProviderSettings(lianjia_transaction_snapshot_path=tmp_path / "absent.json")

    with pytest.raises(ProviderConfigurationError, match="is not an existing file"):
        build_transaction_provider(shanghai_profile, settings)


def test_unknown_provider_name_fails_clearly(recorded_settings: ProviderSettings) -> None:
    profile = CityProfile(
        city_code="testville",
        providers=CityProviderNames(transactions="mystery"),
    )

    with pytest.raises(ProviderConfigurationError, match="unknown transactions provider 'mystery'"):
        build_transaction_provider(profile, recorded_settings)


def test_profile_without_a_transactions_provider_fails_clearly(
    recorded_settings: ProviderSettings,
) -> None:
    profile = CityProfile(city_code="testville")

    with pytest.raises(ProviderConfigurationError, match="names no transactions provider"):
        build_transaction_provider(profile, recorded_settings)


@pytest.mark.parametrize("package", SOURCE_INDEPENDENT_PACKAGES)
def test_core_packages_carry_no_source_specific_code(package: str) -> None:
    pattern = re.compile(r"lianjia|beike|amap", re.IGNORECASE)

    offenders = [
        path.relative_to(PACKAGE_DIR)
        for path in sorted((PACKAGE_DIR / package).rglob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


@pytest.mark.parametrize("package", SOURCE_INDEPENDENT_PACKAGES)
def test_core_packages_do_not_import_the_composition_boundary(package: str) -> None:
    pattern = re.compile(r"cn_property_agent\.config|from \.\.config|from \.config")

    offenders = [
        path.relative_to(PACKAGE_DIR)
        for path in sorted((PACKAGE_DIR / package).rglob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
