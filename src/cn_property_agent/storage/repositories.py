from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterable

import duckdb

from cn_property_agent.domain import Community, EntityAlias, Listing, ListingSnapshot, SourceRef, Transaction
from cn_property_agent.utils import normalize_text


def _row_dict(cursor: duckdb.DuckDBPyConnection, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


def _json_load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class CommunityRepository:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def upsert(self, community: Community) -> None:
        self.connection.execute(
            """
            INSERT INTO community (
                community_id, city_code, canonical_name, normalized_name,
                district, subdistrict, address, latitude, longitude,
                built_year_min, built_year_max, building_types, source_refs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(community_id) DO UPDATE SET
                city_code = excluded.city_code,
                canonical_name = excluded.canonical_name,
                normalized_name = excluded.normalized_name,
                district = excluded.district,
                subdistrict = excluded.subdistrict,
                address = excluded.address,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                built_year_min = excluded.built_year_min,
                built_year_max = excluded.built_year_max,
                building_types = excluded.building_types,
                source_refs = excluded.source_refs,
                updated_at = now()
            """,
            [
                community.community_id,
                community.city_code,
                community.canonical_name,
                normalize_text(community.canonical_name),
                community.district,
                community.subdistrict,
                community.address,
                community.latitude,
                community.longitude,
                community.built_year_min,
                community.built_year_max,
                json.dumps(list(community.building_types), ensure_ascii=False),
                json.dumps([ref.model_dump(mode="json") for ref in community.source_refs], ensure_ascii=False),
            ],
        )

    def get(self, community_id: str) -> Community | None:
        cursor = self.connection.execute(
            """SELECT community_id, city_code, canonical_name, district, subdistrict,
                      address, latitude, longitude, built_year_min, built_year_max,
                      building_types, source_refs
               FROM community WHERE community_id = ?""",
            [community_id],
        )
        row = cursor.fetchone()
        return None if row is None else self._to_model(_row_dict(cursor, row))

    def find_by_normalized_name(self, city_code: str, normalized_name: str) -> list[Community]:
        cursor = self.connection.execute(
            """SELECT community_id, city_code, canonical_name, district, subdistrict,
                      address, latitude, longitude, built_year_min, built_year_max,
                      building_types, source_refs
               FROM community
               WHERE city_code = ? AND normalized_name = ?
               ORDER BY canonical_name, community_id""",
            [city_code, normalized_name],
        )
        return [self._to_model(_row_dict(cursor, row)) for row in cursor.fetchall()]

    def upsert_alias(self, alias: EntityAlias) -> None:
        self.connection.execute(
            """INSERT INTO entity_alias (
                   entity_type, entity_id, provider, provider_entity_id, provider_url
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(entity_type, provider, provider_entity_id) DO UPDATE SET
                   entity_id = excluded.entity_id,
                   provider_url = excluded.provider_url,
                   last_seen_at = now()""",
            [alias.entity_type, alias.entity_id, alias.provider, alias.provider_entity_id, alias.provider_url],
        )

    def find_by_alias(self, *, entity_type: str, provider: str, provider_entity_id: str) -> Community | None:
        cursor = self.connection.execute(
            """SELECT c.community_id, c.city_code, c.canonical_name, c.district,
                      c.subdistrict, c.address, c.latitude, c.longitude,
                      c.built_year_min, c.built_year_max, c.building_types, c.source_refs
               FROM entity_alias a
               JOIN community c ON c.community_id = a.entity_id
               WHERE a.entity_type = ? AND a.provider = ? AND a.provider_entity_id = ?""",
            [entity_type, provider, provider_entity_id],
        )
        row = cursor.fetchone()
        return None if row is None else self._to_model(_row_dict(cursor, row))

    @staticmethod
    def _to_model(data: dict[str, Any]) -> Community:
        data["building_types"] = tuple(_json_load(data["building_types"], []))
        data["source_refs"] = tuple(SourceRef.model_validate(item) for item in _json_load(data["source_refs"], []))
        return Community.model_validate(data)


class TransactionRepository:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def upsert_many(self, transactions: Iterable[Transaction]) -> int:
        count = 0
        for item in transactions:
            self.upsert(item)
            count += 1
        return count

    def upsert(self, item: Transaction) -> None:
        self.connection.execute(
            """INSERT INTO property_transaction VALUES (
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               )
               ON CONFLICT(transaction_id) DO UPDATE SET
                   community_id = excluded.community_id,
                   source = excluded.source,
                   source_transaction_id = excluded.source_transaction_id,
                   source_url = excluded.source_url,
                   deal_date = excluded.deal_date,
                   area_sqm = excluded.area_sqm,
                   layout = excluded.layout,
                   floor_bucket = excluded.floor_bucket,
                   orientation = excluded.orientation,
                   built_year = excluded.built_year,
                   initial_listing_price_cny = excluded.initial_listing_price_cny,
                   deal_price_cny = excluded.deal_price_cny,
                   unit_price_cny_sqm = excluded.unit_price_cny_sqm,
                   days_on_market = excluded.days_on_market,
                   raw_payload_ref = excluded.raw_payload_ref,
                   collected_at = excluded.collected_at,
                   parser_version = excluded.parser_version""",
            [
                item.transaction_id,
                item.community_id,
                item.source,
                item.source_transaction_id,
                item.source_url,
                item.deal_date,
                item.area_sqm,
                item.layout,
                item.floor_bucket.value,
                item.orientation,
                item.built_year,
                item.initial_listing_price_cny,
                item.deal_price_cny,
                item.unit_price_cny_sqm,
                item.days_on_market,
                item.raw_payload_ref,
                item.collected_at,
                item.parser_version,
            ],
        )

    def list_for_community(
        self,
        community_id: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        clauses = ["community_id = ?"]
        params: list[Any] = [community_id]
        if start_date is not None:
            clauses.append("deal_date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("deal_date <= ?")
            params.append(end_date)
        cursor = self.connection.execute(
            f"""SELECT transaction_id, community_id, source, source_transaction_id,
                       source_url, deal_date, area_sqm, layout, floor_bucket, orientation,
                       built_year, initial_listing_price_cny, deal_price_cny,
                       unit_price_cny_sqm, days_on_market, raw_payload_ref,
                       collected_at, parser_version
                FROM property_transaction
                WHERE {' AND '.join(clauses)}
                ORDER BY deal_date DESC, transaction_id""",
            params,
        )
        return [Transaction.model_validate(_row_dict(cursor, row)) for row in cursor.fetchall()]


class ListingRepository:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def upsert_listing(self, item: Listing) -> None:
        self.connection.execute(
            """INSERT INTO listing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(listing_id) DO UPDATE SET
                   community_id = excluded.community_id,
                   source = excluded.source,
                   source_listing_id = excluded.source_listing_id,
                   area_sqm = excluded.area_sqm,
                   layout = excluded.layout,
                   floor_bucket = excluded.floor_bucket,
                   orientation = excluded.orientation,
                   built_year = excluded.built_year,
                   building_type = excluded.building_type,
                   first_seen_at = least(listing.first_seen_at, excluded.first_seen_at),
                   last_seen_at = greatest(listing.last_seen_at, excluded.last_seen_at),
                   status = excluded.status""",
            [
                item.listing_id,
                item.community_id,
                item.source,
                item.source_listing_id,
                item.area_sqm,
                item.layout,
                item.floor_bucket.value,
                item.orientation,
                item.built_year,
                item.building_type,
                item.first_seen_at,
                item.last_seen_at,
                item.status.value,
            ],
        )

    def append_snapshot(self, item: ListingSnapshot) -> None:
        self.connection.execute(
            """INSERT INTO listing_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(listing_id, snapshot_at) DO UPDATE SET
                   list_price_cny = excluded.list_price_cny,
                   unit_price_cny_sqm = excluded.unit_price_cny_sqm,
                   status = excluded.status,
                   source = excluded.source,
                   source_url = excluded.source_url,
                   raw_payload_ref = excluded.raw_payload_ref,
                   parser_version = excluded.parser_version""",
            [
                item.listing_id,
                item.snapshot_at,
                item.list_price_cny,
                item.unit_price_cny_sqm,
                item.status.value,
                item.source,
                item.source_url,
                item.raw_payload_ref,
                item.parser_version,
            ],
        )

    def list_for_community(self, community_id: str) -> list[Listing]:
        """Canonical listing identities of one community, most recently seen first.

        ``listing_id`` is the primary key, so the tie-break makes the order
        total: two calls over unchanged storage return the same sequence.
        """
        cursor = self.connection.execute(
            """SELECT listing_id, community_id, source, source_listing_id, area_sqm,
                      layout, floor_bucket, orientation, built_year, building_type,
                      first_seen_at, last_seen_at, status
               FROM listing
               WHERE community_id = ?
               ORDER BY last_seen_at DESC, listing_id""",
            [community_id],
        )
        return [Listing.model_validate(_row_dict(cursor, row)) for row in cursor.fetchall()]

    def latest_snapshots_for_community(self, community_id: str) -> dict[str, ListingSnapshot]:
        """Newest stored snapshot per listing of one community, keyed by listing_id.

        A listing without any stored snapshot is simply absent from the mapping;
        ``(listing_id, snapshot_at)`` is the primary key, so "newest" is
        unambiguous.
        """
        cursor = self.connection.execute(
            """SELECT s.listing_id, s.snapshot_at, s.list_price_cny, s.unit_price_cny_sqm,
                      s.status, s.source, s.source_url, s.raw_payload_ref, s.parser_version
               FROM listing_snapshot s
               JOIN listing l ON l.listing_id = s.listing_id
               WHERE l.community_id = ?
               QUALIFY row_number() OVER (
                   PARTITION BY s.listing_id ORDER BY s.snapshot_at DESC
               ) = 1""",
            [community_id],
        )
        snapshots = [ListingSnapshot.model_validate(_row_dict(cursor, row)) for row in cursor.fetchall()]
        return {item.listing_id: item for item in snapshots}

    def history(self, listing_id: str) -> list[ListingSnapshot]:
        cursor = self.connection.execute(
            """SELECT listing_id, snapshot_at, list_price_cny, unit_price_cny_sqm,
                      status, source, source_url, raw_payload_ref, parser_version
               FROM listing_snapshot
               WHERE listing_id = ? ORDER BY snapshot_at""",
            [listing_id],
        )
        return [ListingSnapshot.model_validate(_row_dict(cursor, row)) for row in cursor.fetchall()]
