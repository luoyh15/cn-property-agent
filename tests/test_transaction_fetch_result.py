from __future__ import annotations

import pytest
from pydantic import ValidationError

from cn_property_agent.providers import (
    ParseRejection,
    ParseRejectionReason,
    ParseResult,
    RawTransactionRecord,
    SourceRowRef,
    TransactionFetchResult,
)


def make_rejection(row_index: int) -> ParseRejection:
    return ParseRejection(
        row=SourceRowRef(source="fixture", row_index=row_index),
        reason=ParseRejectionReason.MALFORMED_ROW,
        detail="expected a field mapping, got str",
    )


def test_empty_fetch_result_reports_zero_of_everything() -> None:
    result = TransactionFetchResult()

    assert (result.source_row_count, result.parsed_count, result.parse_rejection_count) == (0, 0, 0)


def test_from_records_treats_every_row_as_parsed(
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    records = (provider_records["valid_a"], provider_records["valid_b"])

    result = TransactionFetchResult.from_records(records)

    assert result.records == records
    assert result.parse_rejections == ()
    assert result.source_row_count == result.parsed_count == 2


def test_from_parse_result_keeps_records_and_rejections_together(
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    rejection = make_rejection(1)
    parsed = ParseResult(records=(provider_records["valid_a"],), rejections=(rejection,))

    result = TransactionFetchResult.from_parse_result(parsed)

    assert result.records == parsed.records
    assert result.parse_rejections == (rejection,)
    assert result.source_row_count == 2


def test_source_row_count_may_exceed_parsed_and_rejected_rows(
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    """A provider may drop non-transaction rows before parsing sees them."""
    result = TransactionFetchResult.from_records(
        (provider_records["valid_a"],),
        source_row_count=5,
    )

    assert (result.source_row_count, result.parsed_count) == (5, 1)


def test_source_row_count_may_not_hide_accounted_rows(
    provider_records: dict[str, RawTransactionRecord],
) -> None:
    with pytest.raises(ValidationError, match="smaller than the 2 rows"):
        TransactionFetchResult(
            records=(provider_records["valid_a"],),
            parse_rejections=(make_rejection(1),),
            source_row_count=1,
        )


def test_fetch_result_is_immutable(provider_records: dict[str, RawTransactionRecord]) -> None:
    result = TransactionFetchResult.from_records((provider_records["valid_a"],))

    with pytest.raises(ValidationError):
        result.source_row_count = 7
