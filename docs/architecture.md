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

A `TransactionProvider` therefore returns a `TransactionFetchResult`, not a bare record sequence: parsed DTOs and the batch's `ParseRejection` entries travel together to the service. Its counts each have one meaning — `source_row_count` (rows observed for the request) ≥ `parsed_count` + `parse_rejection_count`; a larger `source_row_count` means the provider discarded rows before parsing. Transport/network failures are not part of the envelope; they propagate and surface as `ProviderFetchError`, so an empty successful fetch is never confused with a broken one.

### City profiles

City profiles bind provider implementations and local market conventions to the city-agnostic core. A profile may define provider names, geography levels, benchmark hierarchy, timezone, currency, source-specific settings and feature availability.

The core domain, storage contracts, analytics equations, service interfaces and MCP tool schemas must not depend on Shanghai-specific imports. Shanghai is the first integration test of the platform architecture.

A profile carries provider *names*, never provider objects. `cn_property_agent.config` is the composition boundary that turns a name plus runtime settings into a concrete adapter — the only place allowed to know that Shanghai's `transactions: lianjia` means the recorded Lianjia provider. Deployment facts such as filesystem paths and credentials come from `ProviderSettings` (environment, `CN_PROPERTY_` prefix) rather than from a committed profile file. Composition fails with `ProviderConfigurationError` when a named provider cannot be constructed — an unknown name, or a Lianjia transaction provider without an existing recorded snapshot path — so a misconfiguration can never reach a service disguised as a source with no data. Layers below the boundary never import `config`.

### Storage

Persists canonical data and provenance. DuckDB is the MVP query engine; Parquet is the durable analytical format.

The key time-series design is `listing_snapshot`: each observation is append-only/idempotent for `(listing_id, snapshot_at/source observation key)`.

### Services

Expose source-independent workflows and caching. Services are the only interfaces used by API, MCP, agent and analytics orchestration.

Ingestion keeps both failure vocabularies visible instead of collapsing them. `TransactionIngestionResult` reports `source_row_count`, `parsed_count`, `upserted_count`, the provider's `parse_rejections` and the canonical `quality_rejections` separately, so `parsed_count == upserted_count + quality_rejection_count` and `rejection_count` is only ever the sum of the two kinds. Parse rejections are never rewritten into canonical `TransactionRejection` values.

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
