# CN Property Agent

China residential property due-diligence and value-preservation research platform.

The project is **city-configurable by design**. Shanghai is the first reference profile used to validate the architecture, data contracts, analytics, and research workflow. City-specific sources, geography, policy conventions, benchmark definitions, and provider settings must live in configuration/profile modules rather than in the core domain or analytics layer.

The system is designed to answer questions such as:

- Is a specific community resilient at the current price?
- How liquid is it relative to nearby comparable communities?
- Are sellers weakening through repeated price cuts and longer time-on-market?
- Is future residential supply likely to pressure resale values?
- How does the community behave relative to its local benchmark during market drawdowns?
- Is a candidate purchase price attractive relative to recent comparable transactions?

The system is **not primarily a house-price prediction model**. It is a research and risk-assessment platform that combines transactions, listing snapshots, official market data, planning/land supply, geospatial data, and web research into explainable conclusions.

## Core principles

1. **City is configuration, not architecture.** Core domain models, analytics, services and agent tools must remain city-agnostic.
2. **Agent never scrapes websites directly.** It calls stable service/tool interfaces.
3. **Providers are replaceable.** Website/API changes should not propagate into analytics or agent logic.
4. **All external data is normalized into shared domain models.**
5. **Listing snapshots are first-class data.** Historical asking-price and inventory dynamics are strategically important.
6. **Analytics are deterministic and testable.** LLMs interpret evidence; they do not calculate core metrics ad hoc.
7. **Reports expose evidence and uncertainty.** Avoid opaque single-number recommendations.
8. **Compliance first.** Respect site terms, robots rules, rate limits, authentication and CAPTCHA boundaries. Never implement CAPTCHA bypass or access-control circumvention.

## Architecture

```text
┌───────────────────────────────────────────────────────────┐
│                    Agent / API / MCP                      │
│ research · compare · evaluate_purchase · report · watch   │
├───────────────────────────────────────────────────────────┤
│                    Analytics Engine                       │
│ price · liquidity · seller pressure · supply · comps      │
│ location scarcity · aging risk · preservation assessment  │
├───────────────────────────────────────────────────────────┤
│                      Data Services                        │
│ community · transaction · listing · geo · market · land   │
├───────────────────────────────────────────────────────────┤
│                       Providers                           │
│ listing/deal · map · local gov · planning · web research  │
├───────────────────────────────────────────────────────────┤
│                     City Profiles                         │
│ Shanghai · Beijing · Shenzhen · ...                       │
├───────────────────────────────────────────────────────────┤
│                        Storage                            │
│ DuckDB · Parquet · raw snapshots · provenance metadata    │
└───────────────────────────────────────────────────────────┘
```

See [AGENTS.md](AGENTS.md) for the implementation contract and [docs/architecture.md](docs/architecture.md) for the detailed design.

## Development automation

The repository supports an owner-only GitHub Issue → local Claude Code → Pull Request workflow using a self-hosted runner.

```text
ChatGPT creates owner [claude] Issue
        ↓
GitHub Actions
        ↓
local self-hosted runner
        ↓
local Claude Code CLI
        ↓
branch + tests + PR
```

Because this is a public repository, the runner revalidates that executable tasks are authored by the repository owner and use the `[claude]` title prefix. Automated tasks cannot modify the runner/security trust-boundary files.

See [docs/claude-runner.md](docs/claude-runner.md) for installation, security, and dispatch instructions.

## City profile concept

A city profile should define only city-specific concerns, for example:

```text
configs/cities/shanghai.yaml

city_code: shanghai
country: CN
currency: CNY
timezone: Asia/Shanghai
providers:
  transactions: lianjia
  listings: lianjia
  geospatial: amap
  market: shanghai_official
  planning: shanghai_planning
benchmarks:
  primary_level: district
```

Core code must never contain logic such as `if city == "shanghai"` for analytical behavior that can be represented through configuration or provider capabilities.

## Suggested stack

- Python 3.12+
- Pydantic
- DuckDB
- Parquet
- Polars
- httpx
- Playwright/Selenium only where a public web UI is the intended source
- FastAPI
- MCP Python SDK
- pytest

Avoid introducing Kafka, Spark, Kubernetes, a vector database, or a multi-agent framework in the MVP unless a demonstrated need appears.

## MVP scope

**Platform:** China-ready, city-configurable architecture.

**Reference profile:** Shanghai only for the first end-to-end implementation:

- historical resale transactions
- current listings + persistent listing snapshots
- official city/district market data
- AMap geospatial / commute data
- land and planning supply signals
- explainable preservation-risk metrics
- community analysis and comparison via API/MCP

After the Shanghai profile is stable, adding another city should mainly require new configuration and provider adapters, not changes to core analytics or agent contracts.

News/social sentiment and ML valuation are Phase 2.

## Candidate open-source references

These are references, not dependencies or submodules:

- `MinjieDING/Lianjia` — recent transaction scraping workflow and useful transaction fields
- `linpingta/lianjia-eroom-analysis` — listing snapshot / historical asking-price analysis idea
- `id5463/lianjia-scraper-analysis` — modern scrape → structured dataset → analysis pipeline
- `agentic-ops/real-estate-mcp` — real-estate MCP/tool architecture reference

The project should reimplement relevant provider logic behind its own interfaces instead of inheriting external project architecture.

## License

Choose an open-source license before publishing implementation code. MIT is a reasonable default for the software, but data-source terms and redistribution restrictions remain separate and must be respected.
