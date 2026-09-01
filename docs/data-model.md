# Data Model Notes

## Why listing snapshots matter

Current listings are ephemeral. For preservation research, price-cut velocity, stale inventory and seller pressure require observing the same listing through time. Therefore:

```text
listing = identity / relatively static attributes
listing_snapshot = observed mutable market state at time t
```

Do not store `current_price` as the only price record.

## Core relationships

```text
community 1 ── * transaction
community 1 ── * listing 1 ── * listing_snapshot
community 1 ── * commute_metric
community * ── * land_parcel (spatial association)
community * ── * research_event
```

## Suggested identifiers

Internal IDs should be UUID/ULID or stable hashes independent from providers. Preserve provider IDs as aliases.

Example alias table:

```text
entity_type
entity_id
provider
provider_entity_id
provider_url
first_seen_at
last_seen_at
```

## Metric observations

Persist computed metrics with their calculation context instead of overwriting:

```text
metric_name
entity_id
as_of
window
value
unit
sample_size
algorithm_version
input_fingerprint
```

This supports reproducibility and model-version comparisons.
