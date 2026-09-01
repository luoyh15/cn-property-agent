"""Runtime provider settings supplied by the environment.

Filesystem paths and credentials are deployment facts, not city facts, so they
live here rather than in a committed city profile. Every value is optional:
whether a missing value is fatal depends on which providers the active city
profile actually names, and that judgement belongs to composition.

Environment variables use the ``CN_PROPERTY_`` prefix, e.g.
``CN_PROPERTY_LIANJIA_TRANSACTION_SNAPSHOT_PATH``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CN_PROPERTY_", extra="ignore", frozen=True)

    lianjia_transaction_snapshot_path: Path | None = None
    """Recorded Lianjia transaction snapshot to replay. Required whenever a
    city profile names the ``lianjia`` transaction provider, because the
    repository has no live Lianjia acquisition path."""
