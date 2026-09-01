from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from cn_property_agent.domain import Community
from cn_property_agent.providers import RawTransactionRecord


class FakeTransactionProvider:
    """In-memory `TransactionProvider` for tests only.

    Configured records are returned verbatim, including out-of-range or
    malformed ones, so that service-side quality gates are what the tests
    actually exercise.
    """

    def __init__(
        self,
        records: Mapping[str, Sequence[RawTransactionRecord]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._records = {key: tuple(value) for key, value in (records or {}).items()}
        self._error = error
        self.calls: list[tuple[str, date | None, date | None]] = []

    async def fetch_transactions(
        self,
        community: Community,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Sequence[RawTransactionRecord]:
        self.calls.append((community.community_id, start_date, end_date))
        if self._error is not None:
            raise self._error
        return self._records.get(community.community_id, ())
