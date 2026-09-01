from __future__ import annotations

from pathlib import Path
from types import TracebackType

import duckdb

from .schema import DDL, SCHEMA_VERSION


class DuckDBDatabase:
    """Small explicit DuckDB lifecycle wrapper for MVP repositories."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = duckdb.connect(self.path)

    def initialize(self) -> None:
        for statement in DDL:
            self.connection.execute(statement)
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
            [SCHEMA_VERSION],
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DuckDBDatabase":
        self.initialize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
