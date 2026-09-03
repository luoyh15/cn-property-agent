"""Failures of the Shanghai official market adapter.

Three things can go wrong, and they are kept apart because they blame different
parties: the caller asked about a city this adapter does not serve, the recorded
dataset could not be read as a batch, or one recorded row could not be turned
into a canonical observation.

All of them raise. None of them is ever answered with an empty batch, because an
empty batch means "the source published nothing for this request" and must stay
distinguishable from "this adapter could not answer".
"""

from __future__ import annotations

from pathlib import Path


class ShanghaiOfficialMarketError(Exception):
    """Base class for every failure of this adapter."""


class UnsupportedCityError(ShanghaiOfficialMarketError):
    """A city this adapter does not publish was requested.

    Raised instead of returning another city's series or an empty batch: both
    would let a caller believe it received an answer about the city it asked
    about.
    """

    def __init__(self, city_code: str, *, supported_city_code: str) -> None:
        super().__init__(
            f"shanghai official market provider serves city {supported_city_code!r},"
            f" not the requested {city_code!r}"
        )
        self.city_code = city_code
        self.supported_city_code = supported_city_code


class ShanghaiOfficialParseError(ShanghaiOfficialMarketError):
    """One recorded row could not be represented as a canonical observation.

    Carries the offending column and row position so the recorded dataset can be
    corrected without re-deriving which row failed.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        row_index: int | None = None,
    ) -> None:
        location = ", ".join(
            part
            for part in (
                None if row_index is None else f"row {row_index}",
                None if field is None else f"column {field!r}",
            )
            if part is not None
        )
        super().__init__(f"{location}: {message}" if location else message)
        self.field = field
        self.row_index = row_index


class ShanghaiOfficialDatasetError(ShanghaiOfficialMarketError):
    """A recorded dataset could not be read as a Shanghai official batch.

    Covers a missing/unreadable file, invalid JSON, a wrong top-level shape,
    invalid batch metadata, a non-array ``rows``, a batch recorded for another
    city, and any row the parser refused.
    """

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"shanghai official market dataset {path}: {message}")
        self.path = path
