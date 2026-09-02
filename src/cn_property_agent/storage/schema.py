from __future__ import annotations

SCHEMA_VERSION = 2

DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp)""",
    """CREATE TABLE IF NOT EXISTS community (
        community_id VARCHAR PRIMARY KEY, city_code VARCHAR NOT NULL, canonical_name VARCHAR NOT NULL,
        normalized_name VARCHAR NOT NULL, district VARCHAR, subdistrict VARCHAR, address VARCHAR,
        latitude DOUBLE, longitude DOUBLE, built_year_min INTEGER, built_year_max INTEGER,
        building_types JSON NOT NULL, source_refs JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
    )""",
    """CREATE INDEX IF NOT EXISTS idx_community_name ON community(city_code, normalized_name)""",
    """CREATE TABLE IF NOT EXISTS entity_alias (
        entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL, provider VARCHAR NOT NULL,
        provider_entity_id VARCHAR NOT NULL, provider_url VARCHAR,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
        PRIMARY KEY(entity_type, provider, provider_entity_id)
    )""",
    """CREATE TABLE IF NOT EXISTS property_transaction (
        transaction_id VARCHAR PRIMARY KEY, community_id VARCHAR NOT NULL, source VARCHAR NOT NULL,
        source_transaction_id VARCHAR, source_url VARCHAR, deal_date DATE NOT NULL,
        area_sqm DOUBLE NOT NULL, layout VARCHAR, floor_bucket VARCHAR NOT NULL, orientation VARCHAR,
        built_year INTEGER, initial_listing_price_cny DOUBLE, deal_price_cny DOUBLE NOT NULL,
        unit_price_cny_sqm DOUBLE NOT NULL, days_on_market INTEGER, raw_payload_ref VARCHAR,
        collected_at TIMESTAMPTZ NOT NULL, parser_version VARCHAR NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_transaction_community_date ON property_transaction(community_id, deal_date)""",
    """CREATE TABLE IF NOT EXISTS listing (
        listing_id VARCHAR PRIMARY KEY, community_id VARCHAR NOT NULL, source VARCHAR NOT NULL,
        source_listing_id VARCHAR NOT NULL, area_sqm DOUBLE, layout VARCHAR, floor_bucket VARCHAR NOT NULL,
        orientation VARCHAR, built_year INTEGER, building_type VARCHAR,
        first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL, UNIQUE(source, source_listing_id)
    )""",
    """CREATE TABLE IF NOT EXISTS listing_snapshot (
        listing_id VARCHAR NOT NULL, snapshot_at TIMESTAMPTZ NOT NULL, list_price_cny DOUBLE NOT NULL,
        unit_price_cny_sqm DOUBLE, status VARCHAR NOT NULL, source VARCHAR NOT NULL, source_url VARCHAR,
        raw_payload_ref VARCHAR, parser_version VARCHAR NOT NULL,
        PRIMARY KEY(listing_id, snapshot_at)
    )""",
    """CREATE TABLE IF NOT EXISTS market_observation (
        observation_id VARCHAR PRIMARY KEY, city_code VARCHAR NOT NULL, geography_type VARCHAR NOT NULL,
        geography_code VARCHAR, geography_name VARCHAR NOT NULL, period_start DATE NOT NULL,
        period_end DATE NOT NULL, metric_name VARCHAR NOT NULL, value DOUBLE NOT NULL, unit VARCHAR NOT NULL,
        source VARCHAR NOT NULL, source_url VARCHAR, publication_date DATE, collected_at TIMESTAMPTZ NOT NULL,
        parser_version VARCHAR NOT NULL, raw_payload_ref VARCHAR
    )""",
    """CREATE INDEX IF NOT EXISTS idx_market_observation_subject
       ON market_observation(city_code, geography_type, metric_name, period_start)""",
    """CREATE TABLE IF NOT EXISTS land_parcel (
        parcel_id VARCHAR PRIMARY KEY, city_code VARCHAR NOT NULL, name VARCHAR, district VARCHAR,
        latitude DOUBLE, longitude DOUBLE, land_use VARCHAR, site_area_sqm DOUBLE, residential_gfa_sqm DOUBLE,
        announced_at DATE, source VARCHAR NOT NULL, source_url VARCHAR, collected_at TIMESTAMPTZ NOT NULL,
        parser_version VARCHAR NOT NULL, raw_payload_ref VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS planning_event (
        event_id VARCHAR PRIMARY KEY, city_code VARCHAR NOT NULL, title VARCHAR NOT NULL, event_type VARCHAR NOT NULL,
        district VARCHAR, occurred_at DATE, published_at DATE, summary VARCHAR, source VARCHAR NOT NULL,
        source_url VARCHAR, collected_at TIMESTAMPTZ NOT NULL, parser_version VARCHAR NOT NULL, raw_payload_ref VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS poi (
        poi_id VARCHAR PRIMARY KEY, city_code VARCHAR NOT NULL, name VARCHAR NOT NULL, category VARCHAR NOT NULL,
        latitude DOUBLE NOT NULL, longitude DOUBLE NOT NULL, address VARCHAR, source VARCHAR NOT NULL,
        source_ref VARCHAR, collected_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS commute_metric (
        commute_id VARCHAR PRIMARY KEY, origin_community_id VARCHAR NOT NULL, destination_name VARCHAR NOT NULL,
        destination_address VARCHAR, destination_latitude DOUBLE, destination_longitude DOUBLE, mode VARCHAR NOT NULL,
        duration_seconds INTEGER NOT NULL, distance_m INTEGER, observed_at TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL, query_assumptions JSON NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS research_event (
        research_event_id VARCHAR PRIMARY KEY, event_type VARCHAR NOT NULL, title VARCHAR NOT NULL, summary VARCHAR,
        occurred_at TIMESTAMPTZ, published_at TIMESTAMPTZ, city_code VARCHAR NOT NULL, district VARCHAR,
        community_id VARCHAR, source_url VARCHAR NOT NULL, source VARCHAR NOT NULL, confidence DOUBLE NOT NULL,
        collected_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS metric_observation (
        metric_id VARCHAR PRIMARY KEY, metric_name VARCHAR NOT NULL, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
        as_of TIMESTAMPTZ NOT NULL, window_label VARCHAR, value DOUBLE, unit VARCHAR, sample_size INTEGER,
        algorithm_version VARCHAR NOT NULL, input_fingerprint VARCHAR NOT NULL, source_record_ids JSON NOT NULL,
        warnings JSON NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_run (
        analysis_run_id VARCHAR PRIMARY KEY, analysis_type VARCHAR NOT NULL, subject_id VARCHAR NOT NULL,
        started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, algorithm_version VARCHAR NOT NULL,
        input_fingerprint VARCHAR NOT NULL, metadata JSON NOT NULL
    )""",
)
