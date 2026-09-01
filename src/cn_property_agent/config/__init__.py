"""City configuration and provider composition.

City profiles name providers; runtime settings supply deployment facts such as
filesystem paths; composition turns the two into concrete provider objects.
Layers below this boundary (services, analytics, domain, storage, API, MCP,
agent) must not import this package.
"""

from .city_profile import (
    DEFAULT_CITY_PROFILE_DIR,
    CityProfile,
    CityProviderNames,
    load_city_profile,
    load_city_profile_file,
)
from .composition import SNAPSHOT_PATH_ENV_VAR, build_transaction_provider
from .errors import CityProfileError, ConfigurationError, ProviderConfigurationError
from .provider_settings import ProviderSettings
from .yaml_mapping import parse_yaml_mapping

__all__ = [
    "DEFAULT_CITY_PROFILE_DIR",
    "SNAPSHOT_PATH_ENV_VAR",
    "CityProfile",
    "CityProfileError",
    "CityProviderNames",
    "ConfigurationError",
    "ProviderConfigurationError",
    "ProviderSettings",
    "build_transaction_provider",
    "load_city_profile",
    "load_city_profile_file",
    "parse_yaml_mapping",
]
