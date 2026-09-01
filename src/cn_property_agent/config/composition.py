"""Composition boundary: city profile name plus settings to a concrete provider.

This is the only place that may know that ``lianjia`` means
:class:`~cn_property_agent.providers.lianjia.RecordedLianjiaTransactionProvider`.
Services receive a constructed :class:`~cn_property_agent.providers.TransactionProvider`
and stay source-independent; nothing here computes or interprets anything.

Anything that would leave a caller with a provider that cannot deliver data —
an unnamed category, an unknown name, a missing or unusable snapshot path —
raises :class:`ProviderConfigurationError`. An empty successful fetch must
never be the way a misconfiguration announces itself.
"""

from __future__ import annotations

from typing import Callable, Mapping

from cn_property_agent.providers import TransactionProvider
from cn_property_agent.providers.lianjia import (
    LIANJIA_SOURCE,
    RecordedLianjiaTransactionProvider,
)

from .city_profile import CityProfile
from .errors import ProviderConfigurationError
from .provider_settings import ProviderSettings

TransactionProviderBuilder = Callable[[CityProfile, ProviderSettings], TransactionProvider]

SNAPSHOT_PATH_ENV_VAR = "CN_PROPERTY_LIANJIA_TRANSACTION_SNAPSHOT_PATH"


def build_transaction_provider(
    profile: CityProfile,
    settings: ProviderSettings,
) -> TransactionProvider:
    """Construct the transaction provider the profile names, or fail loudly."""
    name = profile.providers.transactions
    if name is None:
        raise ProviderConfigurationError(
            f"city profile {profile.city_code!r} names no transactions provider"
        )
    builder = _TRANSACTION_PROVIDER_BUILDERS.get(name)
    if builder is None:
        known = ", ".join(sorted(_TRANSACTION_PROVIDER_BUILDERS))
        raise ProviderConfigurationError(
            f"city profile {profile.city_code!r} names unknown transactions provider "
            f"{name!r}; known providers: {known}"
        )
    return builder(profile, settings)


def _build_recorded_lianjia_transactions(
    profile: CityProfile,
    settings: ProviderSettings,
) -> TransactionProvider:
    snapshot_path = settings.lianjia_transaction_snapshot_path
    if snapshot_path is None:
        raise ProviderConfigurationError(
            f"city profile {profile.city_code!r} names the {LIANJIA_SOURCE!r} transactions "
            f"provider, which replays a recorded snapshot; set {SNAPSHOT_PATH_ENV_VAR} to an "
            "existing snapshot file. Live acquisition is not implemented."
        )
    if not snapshot_path.is_file():
        raise ProviderConfigurationError(
            f"{SNAPSHOT_PATH_ENV_VAR}={snapshot_path} is not an existing file"
        )
    return RecordedLianjiaTransactionProvider(snapshot_path)


_TRANSACTION_PROVIDER_BUILDERS: Mapping[str, TransactionProviderBuilder] = {
    LIANJIA_SOURCE: _build_recorded_lianjia_transactions,
}
