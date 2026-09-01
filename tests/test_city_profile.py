from __future__ import annotations

from pathlib import Path

import pytest

from cn_property_agent.config import (
    CityProfile,
    CityProfileError,
    load_city_profile,
    load_city_profile_file,
    parse_yaml_mapping,
)

CITY_PROFILE_DIR = Path(__file__).parents[1] / "configs" / "cities"


def write_profile(tmp_path: Path, text: str, name: str = "testville.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_shanghai_profile_selects_the_lianjia_transaction_provider() -> None:
    profile = load_city_profile("shanghai", directory=CITY_PROFILE_DIR)

    assert profile.city_code == "shanghai"
    assert profile.display_name == "上海"
    assert profile.providers.transactions == "lianjia"


def test_unconsumed_sections_are_ignored_rather_than_rejected(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path,
        "city_code: testville\nbenchmarks:\n  primary_level: district\nunits:\n  area: sqm\n",
    )

    profile = load_city_profile_file(path)

    assert profile == CityProfile(city_code="testville")


def test_unnamed_provider_categories_stay_none(tmp_path: Path) -> None:
    path = write_profile(tmp_path, "city_code: testville\nproviders:\n  transactions: lianjia\n")

    profile = load_city_profile_file(path)

    assert (profile.providers.transactions, profile.providers.listings) == ("lianjia", None)


def test_missing_profile_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(CityProfileError, match="cannot be read"):
        load_city_profile("nowhere", directory=tmp_path)


def test_profile_for_another_city_fails_clearly(tmp_path: Path) -> None:
    write_profile(tmp_path, "city_code: elsewhere\n", name="testville.yaml")

    with pytest.raises(CityProfileError, match="declares city_code 'elsewhere'"):
        load_city_profile("testville", directory=tmp_path)


def test_profile_without_city_code_fails_clearly(tmp_path: Path) -> None:
    path = write_profile(tmp_path, "providers:\n  transactions: lianjia\n")

    with pytest.raises(CityProfileError, match="city_code"):
        load_city_profile_file(path)


def test_provider_name_must_not_be_empty(tmp_path: Path) -> None:
    path = write_profile(tmp_path, "city_code: testville\nproviders:\n  transactions: ''\n")

    with pytest.raises(CityProfileError, match="providers.transactions"):
        load_city_profile_file(path)


def test_unsupported_profile_structure_fails_clearly(tmp_path: Path) -> None:
    path = write_profile(tmp_path, "city_code: testville\nproviders:\n  - lianjia\n")

    with pytest.raises(CityProfileError, match="sequences are not supported"):
        load_city_profile_file(path)


def test_parse_yaml_mapping_reads_scalars_sections_and_comments() -> None:
    text = "\n".join(
        [
            "# a comment",
            "city_code: testville",
            "display_name: 测试城",
            "",
            "providers:",
            "  transactions: lianjia",
            "  listings: lianjia",
            "quoted: 'value: with colon'",
        ]
    )

    assert parse_yaml_mapping(text) == {
        "city_code": "testville",
        "display_name": "测试城",
        "providers": {"transactions": "lianjia", "listings": "lianjia"},
        "quoted": "value: with colon",
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("city_code testville\n", "expected 'key: value'"),
        ("city_code: a\ncity_code: b\n", "duplicate key"),
        ("providers:\n  transactions: lianjia\n  transactions: beike\n", "duplicate key"),
        ("providers:\n  nested:\n    deeper: x\n", "nesting deeper than one level"),
        ("providers:\n  transactions: lianjia\n   listings: lianjia\n", "inconsistent indentation"),
        ("  transactions: lianjia\n", "no parent section"),
        ("city_code: a\n\tproviders: x\n", "tab indentation"),
    ],
)
def test_parse_yaml_mapping_rejects_unsupported_input(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_yaml_mapping(text)
