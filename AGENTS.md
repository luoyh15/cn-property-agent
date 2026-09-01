# AGENTS.md — Implementation Contract

This file is the primary instruction set for coding agents working on this repository.

## 1. Mission

Build an explainable China residential property due-diligence platform focused on **value preservation, liquidity and downside risk**. City-specific market rules and data sources must be supplied through configuration/profiles; Shanghai is the first reference profile.

The system should help a buyer research a community, compare communities, and evaluate a candidate purchase price using current and historical evidence.

Shanghai is the first reference city, but **city is configuration**. Do not encode Shanghai as a core package assumption. New cities should be added primarily through city profiles and provider adapters.

Do **not** optimize for generic real-estate chat, SEO content, or black-box price prediction.

## 2. Non-goals for MVP

Do not implement these unless explicitly requested:

- implementing every Chinese city in the MVP
- autonomous financial decisions
- automatic bidding or brokerage actions
- CAPTCHA bypass, credential rotation, anti-bot evasion, or access-control circumvention
- large-scale redistribution of third-party listing data
- opaque end-to-end ML preservation scores
- vector database or RAG infrastructure without a concrete retrieval requirement
- complex multi-agent orchestration

## 2.1 City configuration rule

City-specific concerns belong under configuration/profile modules or concrete providers. The following must remain city-agnostic:

- canonical domain models
- storage interfaces
- analytical metric definitions
- service contracts
- API/MCP tool schemas
- agent research workflow

Avoid `if city == ...` branches in core analytics. Prefer profile capabilities, benchmark configuration and provider dependency injection.

## 3. Architectural rule

Dependencies must flow downward only:

```text
agent/api/mcp
    ↓
services
    ↓
analytics + domain
    ↓
storage / provider interfaces
    ↓
concrete providers
```

The agent layer MUST NOT import scraping/browser code.

The analytics layer MUST NOT call external APIs.

Providers MUST NOT calculate preservation scores.

## 4. Package layout

```text
src/cn_property_agent/
├── domain/       # canonical Pydantic models and enums
├── providers/    # external source adapters
├── storage/      # DuckDB/Parquet repositories and provenance
├── services/     # source-independent business/data access APIs
├── analytics/    # deterministic metric computation
├── agent/        # research plans, evidence assembly, report synthesis
├── api/          # FastAPI routes
└── mcp/          # MCP tools
```

Jobs are entrypoints for scheduled acquisition and snapshotting.

## 5. Canonical domain models

The first implementation task is to define stable models. At minimum:

### Community

Fields:

- `community_id`: internal stable identifier
- `canonical_name`
- `district`
- `subdistrict`
- `address`
- `latitude`
- `longitude`
- `built_year_min`, `built_year_max`
- `building_types`
- `source_refs`

### Property / Unit descriptor

Use only when a stable unit can be identified. Do not fabricate identity from noisy listing text.

### Transaction

Required fields:

- `transaction_id`
- `community_id`
- `source`
- `source_url`
- `deal_date`
- `area_sqm`
- `layout`
- `floor_bucket`
- `orientation`
- `built_year`
- `initial_listing_price_cny`
- `deal_price_cny`
- `unit_price_cny_sqm`
- `days_on_market`
- `raw_payload_ref`
- `collected_at`

Derived values such as negotiation discount must not overwrite raw fields.

### Listing

Stable identity where possible:

- `listing_id`
- `community_id`
- `source`
- `source_listing_id`
- quasi-static unit descriptors
- first seen / last seen
- current status

### ListingSnapshot

This is a first-class table:

- `listing_id`
- `snapshot_at`
- `list_price_cny`
- `unit_price_cny_sqm`
- `status`
- `source_url`
- `raw_payload_ref`

Never model listing price history by mutating one listing row.

### MarketObservation

Official city/district-level observations with:

- geography
- period
- metric name
- value
- unit
- source
- publication date

### LandParcel / PlanningEvent

Capture location, land use, planned residential scale when available, dates, and source provenance.

### POI / CommuteMetric

Store coordinates and exact query assumptions. Commute time must include transport mode, destination and observation timestamp.

### ResearchEvent

For policy/news/qualitative evidence:

- event type
- title/summary
- occurred_at / published_at
- geography/community association
- source URL
- confidence

## 6. Provenance is mandatory

Every externally sourced record must preserve enough provenance to answer:

> Where did this number come from, and when was it observed?

At minimum store:

- source/provider name
- source URL or source identifier
- collected_at
- raw payload/page snapshot reference when legally and operationally appropriate
- parser version

Analytics results should expose the source record IDs used.

## 7. Provider contracts

Implement protocols/ABCs before concrete adapters.

Suggested interfaces:

```python
class TransactionProvider(Protocol):
    async def fetch_transactions(...): ...

class ListingProvider(Protocol):
    async def fetch_current_listings(...): ...

class GeoProvider(Protocol):
    async def geocode(...): ...
    async def nearby_poi(...): ...
    async def commute(...): ...

class MarketProvider(Protocol):
    async def fetch_market_observations(...): ...

class PlanningProvider(Protocol):
    async def fetch_land_supply(...): ...
```

Provider implementations may use APIs, HTTP, or browser automation, but must return canonical models or provider DTOs that are normalized immediately.

## 8. Provider compliance policy

For public-web providers:

- obey source terms and applicable robots/rate limits
- use conservative request rates
- stop when authentication/verification requires user action
- manual CAPTCHA completion may be supported by a headed browser workflow
- never implement automated CAPTCHA solving, credential farming, account rotation, fingerprint spoofing, or bypass techniques
- do not assume scraped data may be republished; separate analysis rights from redistribution rights

## 9. Storage strategy

MVP storage:

- DuckDB for queryable local state
- Parquet for durable analytical datasets
- optional raw HTML/JSON snapshots only when justified and compliant

Use migrations/schema versioning from the start.

Recommended logical tables:

```text
community
property
transaction
listing
listing_snapshot
market_observation
land_parcel
planning_event
poi
commute_metric
research_event
metric_observation
analysis_run
```

## 10. Identity resolution

Community identity is a core problem.

Implement a `CommunityResolver` that uses:

1. provider-native IDs when present
2. normalized canonical names
3. district/subdistrict
4. coordinates/address

Never merge communities using name-only fuzzy matching without geographic confirmation.

Store aliases separately.

## 11. Analytics: deterministic first

All core metrics should be pure/testable functions over normalized data.

### 11.1 Price resilience

Measure community performance relative to suitable benchmark(s), especially during market drawdowns.

Possible outputs:

- 3/6/12/24m median transaction unit-price change
- repeat/similar-unit price trend where sample permits
- excess return vs district/subdistrict benchmark
- drawdown beta / downside capture

Do not calculate a trend when sample size is below a configured threshold; return insufficient evidence.

### 11.2 Liquidity

Outputs may include:

- transactions per month
- median days on market
- active listings
- months of inventory

```text
months_of_inventory = active_listings / trailing_monthly_transactions
```

Handle zero/low transaction counts explicitly rather than returning infinity without context.

### 11.3 Seller pressure

From listing snapshots:

- share of active listings with price cuts
- median cumulative cut from first-seen price
- number/frequency of cuts
- new-listing velocity
- stale-listing share
- withdrawal/down-listing rate

### 11.4 Transaction negotiation

When both initial list price and deal price are observed:

```text
negotiation_discount = (initial_listing_price - deal_price) / initial_listing_price
```

Report distribution, not just mean.

### 11.5 Supply pressure

Use nearby active/new-home supply and future residential land/planning when available.

The initial model should be interpretable and radius-aware (e.g. 1 km / 3 km), not a learned latent score.

### 11.6 Location scarcity

Prefer durable constraints/features over raw POI counts:

- rail accessibility
- commute to configured employment centers
- mature built-out urban fabric
- limited nearby future residential land
- access to high-value public services where data is reliable

Avoid double-counting highly correlated POI features.

### 11.7 Aging / product obsolescence risk

Potential factors:

- building age
- elevator / parking / unit-design limitations when reliably available
- mismatch versus nearby newer competing stock
- maintenance/renovation burden signals

Keep factual observations separate from subjective inference.

## 12. Preservation assessment

Do not begin with a trained model.

First expose a structured assessment:

```text
price_resilience
liquidity
seller_strength
supply_pressure
location_scarcity
product_obsolescence
market_regime
confidence
```

An optional composite score may be added later, but it MUST expose component values, weights, sample sizes and evidence quality.

The output must distinguish:

- observed facts
- calculated metrics
- model/heuristic interpretation
- missing information

## 13. Comparable selection

Implement a comparable-selection service before sophisticated valuation.

For a target transaction/purchase, choose comparables using:

- same community first
- similar area/layout
- similar floor bucket
- similar building age/type
- recent date priority
- nearby comparable communities only when same-community evidence is weak

Return why each comparable was selected and an effective sample size.

## 14. Purchase evaluation

A purchase evaluation should accept:

```text
community
candidate_price
area/layout/unit attributes when available
buyer-specific commute destinations (optional)
lookback horizon
```

It should output:

- recent comparable range (P25/P50/P75 when sample supports it)
- candidate premium/discount to comps
- liquidity state
- seller pressure state
- broader market regime
- supply/location/product risks
- downside scenarios
- confidence and missing evidence

Avoid categorical “buy/sell” language unless the user explicitly requests an opinion; prefer decision-relevant risk framing.

## 15. Service layer

Suggested stable services:

```python
resolve_community(query)
get_community_profile(community_id)
get_transactions(community_id, start, end)
get_current_listings(community_id)
get_listing_history(community_id, start, end)
get_market_context(community_id, period)
get_land_supply(community_id, radius_m)
get_location_metrics(community_id, destinations)
get_preservation_metrics(community_id, as_of)
find_comparables(...)
evaluate_purchase(...)
compare_communities(...)
```

Services coordinate repositories/providers and caching. They should be usable independently of any LLM.

## 16. Agent behavior

The agent is a research orchestrator and synthesizer.

For `analyze community X`, use a plan similar to:

```text
1. resolve community
2. fetch recent transactions
3. fetch current listings
4. fetch listing history
5. fetch benchmark market data
6. fetch nearby land/planning supply
7. fetch location/commute metrics
8. compute deterministic metrics
9. retrieve material recent policy/event evidence if relevant
10. assemble evidence packet
11. generate report
```

The LLM should not invent missing numbers. If a tool has insufficient data, state that explicitly.

## 17. MCP surface

Initial MCP tools:

```text
resolve_community
analyze_community
compare_communities
evaluate_purchase
get_transactions
get_listing_market
get_market_context
get_land_supply
get_location_metrics
```

Prefer higher-level tools for common agent workflows but keep lower-level tools available for evidence inspection.

Every tool response should include:

- structured result
- `as_of`
- sample size where relevant
- source/provenance IDs
- warnings / missing-data flags

## 18. API surface

Initial FastAPI routes:

```text
GET  /communities/search
GET  /communities/{id}
GET  /communities/{id}/transactions
GET  /communities/{id}/listings
GET  /communities/{id}/metrics
POST /analysis/community
POST /analysis/compare
POST /analysis/purchase
```

API DTOs should reuse domain/service schemas where sensible, but do not leak provider-specific structures.

## 19. Acquisition jobs

Initial jobs:

### `sync_transactions`
Backfill and incrementally refresh historical transactions.

### `snapshot_listings`
Critical recurring job. Persist every observed listing state; never just overwrite current state.

### `sync_market`
Fetch official monthly/weekly market observations.

### `sync_planning`
Refresh land and planning events at a lower cadence.

All jobs must be idempotent.

## 20. Data quality gates

Before analytics consumes a record, validate:

- valid configured city/geography
- positive price/area
- consistent total/unit price within tolerance where both exist
- dates not in the future beyond source publication semantics
- duplicate detection
- community resolution confidence

Flag suspicious data; do not silently repair uncertain records.

## 21. Testing strategy

Required test categories:

1. parser fixtures for each provider
2. domain validation
3. identity resolution
4. metric unit tests with hand-computed examples
5. low-sample/zero-sample behavior
6. service integration with fake providers
7. MCP/API schema tests

Do not write tests that depend on live websites for CI.

## 22. Observability

Log:

- provider requests and status classes (without secrets)
- parser version
- records inserted/updated/rejected
- job duration
- CAPTCHA/auth/manual-action pauses
- metric sample sizes

Never log credentials, cookies, full personal identifiers, or secrets.

## 23. MVP implementation order

### Milestone 1 — Foundation

- package skeleton
- domain models
- DuckDB schema/repositories
- provenance model
- community resolver

### Milestone 2 — Transaction path

- one compliant transaction provider for the Shanghai profile
- ingestion + parser fixtures
- transaction query service
- transaction analytics

### Milestone 3 — Listing snapshot path

- current listing provider
- stable listing identity strategy
- recurring snapshot storage
- seller-pressure + inventory metrics

### Milestone 4 — Context

- AMap integration
- official market provider
- planning/land provider
- benchmark and supply metrics

### Milestone 5 — Research product

- comparable selector
- preservation assessment
- purchase evaluation
- compare communities

### Milestone 6 — Interfaces

- FastAPI
- MCP server
- research report generation

## 24. Definition of MVP done

The MVP is done when, for a configured set of communities in the Shanghai reference profile, the system can reproducibly answer:

1. What has recently transacted and at what prices?
2. What is currently listed and how has asking price/inventory changed?
3. How liquid is the community?
4. How does it perform relative to its benchmark?
5. Is seller pressure increasing or decreasing?
6. What nearby future supply and location constraints matter?
7. How does a candidate purchase price compare with evidence-backed comps?
8. What are the strongest preservation arguments and downside risks?
9. How strong is the evidence behind each conclusion?

## 25. Coding style

- type annotations everywhere practical
- Pydantic for boundary/domain validation
- small composable functions
- explicit units in field names (`_cny`, `_sqm`, `_days`, `_m`)
- UTC for machine timestamps; retain local market date semantics separately
- no hidden global mutable state
- provider configuration via environment/settings objects
- secrets never committed

## 26. First task for a coding agent

Start by implementing only:

1. canonical Pydantic domain models
2. provider Protocols
3. DuckDB schema and repositories
4. community resolver interface
5. fixture-based tests

Do not begin scraping or agent prompting until these contracts are stable.
