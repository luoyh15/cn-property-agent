from __future__ import annotations

import pytest
from pydantic import ValidationError

from cn_property_agent.domain import ListingObservation
from cn_property_agent.providers import (
    ListingFetchResult,
    ListingParseResult,
    ParseRejection,
    ParseRejectionReason,
    SourceRowRef,
)
from cn_property_agent.providers.lianjia import LianjiaListingParseContext, parse_listing_row


@pytest.fixture
def observation(
    lianjia_listing_rows: dict[str, dict],
    lianjia_listing_context: LianjiaListingParseContext,
) -> ListingObservation:
    parsed = parse_listing_row(lianjia_listing_rows["valid_full"], context=lianjia_listing_context)
    assert isinstance(parsed, ListingObservation)
    return parsed


def make_rejection(row_index: int) -> ParseRejection:
    return ParseRejection(
        row=SourceRowRef(source="fixture", row_index=row_index),
        reason=ParseRejectionReason.MALFORMED_ROW,
        detail="expected a field mapping, got str",
    )


def test_empty_fetch_result_reports_zero_of_everything() -> None:
    result = ListingFetchResult()

    assert (result.source_row_count, result.parsed_count, result.parse_rejection_count) == (0, 0, 0)


def test_from_observations_treats_every_row_as_parsed(observation: ListingObservation) -> None:
    result = ListingFetchResult.from_observations((observation,))

    assert result.observations == (observation,)
    assert result.parse_rejections == ()
    assert result.source_row_count == result.parsed_count == 1


def test_from_parse_result_keeps_observations_and_rejections_together(
    observation: ListingObservation,
) -> None:
    rejection = make_rejection(1)
    parsed = ListingParseResult(observations=(observation,), rejections=(rejection,))

    result = ListingFetchResult.from_parse_result(parsed)

    assert result.observations == parsed.observations
    assert result.parse_rejections == (rejection,)
    assert result.source_row_count == 2


def test_source_row_count_may_exceed_parsed_and_rejected_rows(
    observation: ListingObservation,
) -> None:
    """A provider may drop non-listing rows before parsing sees them."""
    result = ListingFetchResult.from_observations((observation,), source_row_count=5)

    assert (result.source_row_count, result.parsed_count) == (5, 1)


def test_source_row_count_may_not_hide_accounted_rows(observation: ListingObservation) -> None:
    with pytest.raises(ValidationError, match="smaller than the 2 rows"):
        ListingFetchResult(
            observations=(observation,),
            parse_rejections=(make_rejection(1),),
            source_row_count=1,
        )


def test_fetch_result_is_immutable(observation: ListingObservation) -> None:
    result = ListingFetchResult.from_observations((observation,))

    with pytest.raises(ValidationError):
        result.source_row_count = 7
