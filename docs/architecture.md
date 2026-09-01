# Architecture

## Objective

Build a unified research system for China resale residential properties with explicit city profiles. Shanghai is the first reference implementation, not a hard-coded architectural assumption. The architecture separates evidence acquisition from calculations and separates both from LLM interpretation.

## Layer responsibilities

### Providers

Translate external sources into normalized source DTOs/canonical records.

Examples for the Shanghai reference profile:

- Lianjia/Beike: transactions and listings
- Shanghai official sources: market observations, policies
- Shanghai land/planning sources: future residential supply
- AMap: geocoding, POI and commute
- Web research: recent material events

Equivalent providers for another city must satisfy the same source-independent contracts.

A provider is replaceable. A provider failure must not redefine domain objects.

Acquisition (transport/extraction) and interpretation (parsing) are separate steps. A parser consumes an already extracted field mapping plus provenance context and returns a `ParseResult`: successfully parsed source DTOs plus per-row `ParseRejection` entries. One unintelligible row must never discard the rest of a batch, and a rejection identifies its row (source, row index, provider-native id, URL, payload reference) without copying the payload.

Parse failures and data-quality rejections are different vocabularies. A parser rejects only what it cannot interpret; plausibility checks — positive price/area, dates in range, total/unit price consistency — stay in the service-layer quality gates.

### City profiles

City profiles bind provider implementations and local market conventions to the city-agnostic core. A profile may define provider names, geography levels, benchmark hierarchy, timezone, currency, source-specific settings and feature availability.

The core domain, storage contracts, analytics equations, service interfaces and MCP tool schemas must not depend on Shanghai-specific imports. Shanghai is the first integration test of the platform architecture.

### Storage

Persists canonical data and provenance. DuckDB is the MVP query engine; Parquet is the durable analytical format.

The key time-series design is `listing_snapshot`: each observation is append-only/idempotent for `(listing_id, snapshot_at/source observation key)`.

### Services

Expose source-independent workflows and caching. Services are the only interfaces used by API, MCP, agent and analytics orchestration.

### Analytics

Pure/deterministic computations over normalized records. No network calls and no free-form LLM calculations.

### Agent

Chooses which service tools to call, determines whether evidence is sufficient, and writes an evidence-linked report. It must distinguish fact, calculation and interpretation.

## Research flow

```text
User query
   ↓
Intent: analyze / compare / evaluate purchase
   ↓
CommunityResolver
   ↓
Parallel evidence acquisition via services
   ├─ transactions
   ├─ listings + history
   ├─ benchmark market
   ├─ land/planning
   └─ geo/commute
   ↓
Analytics Engine
   ├─ comparable set
   ├─ price resilience
   ├─ liquidity
   ├─ seller pressure
   ├─ supply pressure
   └─ location/product risks
   ↓
Evidence Packet
   ↓
LLM synthesis
   ↓
Structured report + provenance + confidence
```

## Evidence packet

The agent should receive a compact structured object rather than raw pages. Example:

```json
{
  "subject": {"community_id": "...", "name": "..."},
  "as_of": "...",
  "transactions": {"n": 18, "summary": {}, "records": []},
  "listings": {"active": 41, "history_summary": {}},
  "metrics": {},
  "benchmarks": {},
  "supply": {},
  "location": {},
  "warnings": [],
  "provenance": []
}
```

The report generator should not be responsible for retrieving missing data during rendering; research planning should complete first.

## Confidence

Confidence is evidence quality, not model certainty alone. Consider:

- number and recency of transactions
- share of records with initial listing price / days on market
- completeness of listing history
- benchmark availability
- community resolution confidence
- planning-data freshness

Prefer categorical grades (high/medium/low) with reasons before attempting calibrated probabilities.

## Security and compliance

- secrets through environment or secret manager
- no committed cookies/tokens
- no automated bypass of verification or authentication boundaries
- provider-specific rate limiting
- raw snapshots separated from derived/public outputs
- clearly document data redistribution limitations
