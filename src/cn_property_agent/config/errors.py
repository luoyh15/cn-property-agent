from __future__ import annotations


class ConfigurationError(Exception):
    """Base class for failures raised while reading configuration."""


class CityProfileError(ConfigurationError):
    """A city profile file could not be read as a profile.

    Covers a missing/unreadable file, an unsupported file structure, invalid
    profile fields and a profile whose ``city_code`` contradicts the city it
    was loaded for.
    """

    def __init__(self, path: object, message: str) -> None:
        super().__init__(f"city profile {path}: {message}")
        self.path = path


class ProviderConfigurationError(ConfigurationError):
    """A provider named by a city profile cannot be constructed.

    Raised at composition time instead of returning a degraded provider, so
    that missing provider settings surface as a configuration failure rather
    than as a source that successfully reports no data.
    """
