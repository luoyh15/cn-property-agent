from __future__ import annotations

from datetime import UTC, date, timedelta
from enum import StrEnum

from pydantic import ValidationError

from cn_property_agent.domain import Community, FloorBucket, FrozenModel, Transaction
from cn_property_agent.providers import RawTransactionRecord
from cn_property_agent.utils import stable_id

DEFAULT_UNIT_PRICE_TOLERANCE = 0.02
"""Relative tolerance between ``unit_price * area`` and the reported deal price."""

FUTURE_DEAL_DATE_GRACE = timedelta(days=1)
"""Local market dates may run ahead of a UTC collection timestamp by one day."""


class RejectionReason(StrEnum):
    MISSING_SOURCE_IDENTITY = "missing_source_identity"
    MISSING_DEAL_DATE = "missing_deal_date"
    DEAL_DATE_IN_FUTURE = "deal_date_in_future"
    DEAL_DATE_OUT_OF_RANGE = "deal_date_out_of_range"
    INVALID_AREA = "invalid_area"
    INVALID_DEAL_PRICE = "invalid_deal_price"
    INVALID_UNIT_PRICE = "invalid_unit_price"
    INVALID_LISTING_PRICE = "invalid_listing_price"
    INVALID_DAYS_ON_MARKET = "invalid_days_on_market"
    INCONSISTENT_UNIT_PRICE = "inconsistent_unit_price"
    DUPLICATE_IN_BATCH = "duplicate_in_batch"
    SCHEMA_INVALID = "schema_invalid"


class TransactionRejection(FrozenModel):
    """A provider record that failed a data-quality gate.

    Rejections are reported, never silently repaired or dropped.
    """

    source: str
    source_transaction_id: str | None = None
    source_url: str | None = None
    reason: RejectionReason
    detail: str


class NormalizedTransaction(FrozenModel):
    transaction: Transaction
    warnings: tuple[str, ...] = ()


def build_transaction_id(source: str, source_transaction_id: str) -> str:
    return stable_id("tx", source, source_transaction_id)


def normalize_transaction(
    record: RawTransactionRecord,
    *,
    community: Community,
    start_date: date | None = None,
    end_date: date | None = None,
    unit_price_tolerance: float = DEFAULT_UNIT_PRICE_TOLERANCE,
) -> NormalizedTransaction | TransactionRejection:
    """Validate one provider record against a resolved community.

    Pure and deterministic: no clock, no I/O. The collection timestamp carried
    by the record is the reference point for "is this date plausible".
    """
    if not record.source_transaction_id:
        return _reject(
            record,
            RejectionReason.MISSING_SOURCE_IDENTITY,
            "record has no stable source_transaction_id",
        )

    if record.deal_date is None:
        return _reject(record, RejectionReason.MISSING_DEAL_DATE, "deal_date is missing")

    observed_on = record.collected_at.astimezone(UTC).date()
    if record.deal_date > observed_on + FUTURE_DEAL_DATE_GRACE:
        return _reject(
            record,
            RejectionReason.DEAL_DATE_IN_FUTURE,
            f"deal_date {record.deal_date} is after collected_at date {observed_on}",
        )

    if start_date is not None and record.deal_date < start_date:
        return _reject(
            record,
            RejectionReason.DEAL_DATE_OUT_OF_RANGE,
            f"deal_date {record.deal_date} is before requested start {start_date}",
        )
    if end_date is not None and record.deal_date > end_date:
        return _reject(
            record,
            RejectionReason.DEAL_DATE_OUT_OF_RANGE,
            f"deal_date {record.deal_date} is after requested end {end_date}",
        )

    if record.area_sqm is None or record.area_sqm <= 0:
        return _reject(
            record,
            RejectionReason.INVALID_AREA,
            f"area_sqm must be positive, got {record.area_sqm}",
        )

    if record.deal_price_cny is None or record.deal_price_cny <= 0:
        return _reject(
            record,
            RejectionReason.INVALID_DEAL_PRICE,
            f"deal_price_cny must be positive, got {record.deal_price_cny}",
        )

    if record.initial_listing_price_cny is not None and record.initial_listing_price_cny <= 0:
        return _reject(
            record,
            RejectionReason.INVALID_LISTING_PRICE,
            f"initial_listing_price_cny must be positive when present, got {record.initial_listing_price_cny}",
        )

    if record.days_on_market is not None and record.days_on_market < 0:
        return _reject(
            record,
            RejectionReason.INVALID_DAYS_ON_MARKET,
            f"days_on_market must not be negative, got {record.days_on_market}",
        )

    warnings: list[str] = []
    unit_price = record.unit_price_cny_sqm
    if unit_price is None:
        unit_price = record.deal_price_cny / record.area_sqm
        warnings.append("unit_price_cny_sqm derived from deal_price_cny and area_sqm")
    elif unit_price <= 0:
        return _reject(
            record,
            RejectionReason.INVALID_UNIT_PRICE,
            f"unit_price_cny_sqm must be positive when present, got {unit_price}",
        )
    else:
        deviation = abs(unit_price * record.area_sqm - record.deal_price_cny) / record.deal_price_cny
        if deviation > unit_price_tolerance:
            return _reject(
                record,
                RejectionReason.INCONSISTENT_UNIT_PRICE,
                f"unit_price_cny_sqm * area_sqm deviates from deal_price_cny by {deviation:.2%}"
                f" (tolerance {unit_price_tolerance:.2%})",
            )

    floor_bucket = _coerce_floor_bucket(record.floor_bucket, warnings)

    try:
        transaction = Transaction(
            transaction_id=build_transaction_id(record.source, record.source_transaction_id),
            community_id=community.community_id,
            source=record.source,
            source_transaction_id=record.source_transaction_id,
            source_url=record.source_url,
            deal_date=record.deal_date,
            area_sqm=record.area_sqm,
            layout=record.layout,
            floor_bucket=floor_bucket,
            orientation=record.orientation,
            built_year=record.built_year,
            initial_listing_price_cny=record.initial_listing_price_cny,
            deal_price_cny=record.deal_price_cny,
            unit_price_cny_sqm=unit_price,
            days_on_market=record.days_on_market,
            raw_payload_ref=record.raw_payload_ref,
            collected_at=record.collected_at,
            parser_version=record.parser_version,
        )
    except ValidationError as error:
        return _reject(record, RejectionReason.SCHEMA_INVALID, _format_validation_error(error))

    return NormalizedTransaction(transaction=transaction, warnings=tuple(warnings))


def _coerce_floor_bucket(value: str | None, warnings: list[str]) -> FloorBucket:
    if value is None:
        return FloorBucket.UNKNOWN
    try:
        return FloorBucket(value.strip().casefold())
    except ValueError:
        warnings.append(f"unrecognized floor_bucket {value!r} stored as {FloorBucket.UNKNOWN.value}")
        return FloorBucket.UNKNOWN


def _format_validation_error(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


def _reject(record: RawTransactionRecord, reason: RejectionReason, detail: str) -> TransactionRejection:
    return TransactionRejection(
        source=record.source,
        source_transaction_id=record.source_transaction_id,
        source_url=record.source_url,
        reason=reason,
        detail=detail,
    )
