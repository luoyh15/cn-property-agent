# MVP Roadmap

## Phase 0 — Contracts

Deliver:

- domain models
- provider protocols
- storage schema
- provenance
- unit conventions
- test fixtures

Exit criterion: analytics/tests can run entirely against synthetic/fixed fixture data.

## Phase 1 — Transactions

Deliver:

- one historical-transaction adapter for the Shanghai profile
- compliant manual verification workflow if needed
- backfill command
- transaction statistics
- comparable selection v1

Exit criterion: given a community, return recent transaction distribution with provenance.

## Phase 2 — Listing snapshots

Deliver:

- listing ingestion
- stable listing ID
- scheduled snapshots
- inventory, price-cut, stale-listing metrics

Exit criterion: after repeated snapshots, reconstruct a listing's asking-price path and community-level seller pressure.

## Phase 3 — Market/location/supply

Deliver:

- official market observations for the Shanghai profile
- AMap geocoding/commute
- nearby land/planning data
- district/subdistrict benchmarks

Exit criterion: community analysis includes market-relative performance and future supply context.

## Phase 4 — Decision engine

Deliver:

- preservation assessment
- purchase-price evaluation
- community comparison
- scenario framing

Exit criterion: a report separates facts, metrics, interpretation, risks, confidence and missing data.

## Phase 5 — Agent interfaces

Deliver:

- MCP server
- FastAPI
- CLI
- report renderer

Exit criterion: external agents can call stable tools without access to provider internals.
