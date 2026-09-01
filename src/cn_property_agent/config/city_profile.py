"""City profiles: the configuration boundary that binds a city to providers.

A profile names the provider implementations and local market conventions of
one city. It carries names only, never provider objects, so nothing below the
composition boundary learns which city or which source is in play.

Sections the platform does not consume yet (``benchmarks``, ``units``) are
read but ignored, so a profile file may stay ahead of the code without
failing to load.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import CityProfileError
from .yaml_mapping import parse_yaml_mapping

DEFAULT_CITY_PROFILE_DIR = Path("configs/cities")


class CityProviderNames(BaseModel):
    """Provider names bound to a city, one per source category.

    A name is a selector resolved at composition time; an unnamed category
    means the city has no provider configured for it yet, which is a
    configuration fact rather than an error here.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    transactions: str | None = Field(default=None, min_length=1)
    listings: str | None = Field(default=None, min_length=1)
    geospatial: str | None = Field(default=None, min_length=1)
    market: str | None = Field(default=None, min_length=1)
    planning: str | None = Field(default=None, min_length=1)
    research: str | None = Field(default=None, min_length=1)


class CityProfile(BaseModel):
    """One city's configuration as loaded from ``configs/cities/<city>.yaml``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    city_code: str = Field(min_length=1)
    country_code: str | None = Field(default=None, min_length=1)
    display_name: str | None = Field(default=None, min_length=1)
    timezone: str | None = Field(default=None, min_length=1)
    currency: str | None = Field(default=None, min_length=1)
    providers: CityProviderNames = Field(default_factory=CityProviderNames)


def load_city_profile(
    city_code: str,
    *,
    directory: Path | str = DEFAULT_CITY_PROFILE_DIR,
) -> CityProfile:
    """Load ``<directory>/<city_code>.yaml`` and verify it describes that city."""
    path = Path(directory) / f"{city_code}.yaml"
    return load_city_profile_file(path, expected_city_code=city_code)


def load_city_profile_file(
    path: Path | str,
    *,
    expected_city_code: str | None = None,
) -> CityProfile:
    """Read and validate one profile file, or raise :class:`CityProfileError`."""
    profile_path = Path(path)
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CityProfileError(profile_path, f"cannot be read: {error}") from error
    except UnicodeDecodeError as error:
        raise CityProfileError(profile_path, f"is not valid UTF-8: {error}") from error

    try:
        payload = parse_yaml_mapping(text)
    except ValueError as error:
        raise CityProfileError(profile_path, str(error)) from error

    try:
        profile = CityProfile.model_validate(payload)
    except ValidationError as error:
        raise CityProfileError(profile_path, _format_validation_error(error)) from error

    if expected_city_code is not None and profile.city_code != expected_city_code:
        raise CityProfileError(
            profile_path,
            f"declares city_code {profile.city_code!r}, expected {expected_city_code!r}",
        )
    return profile


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
