# MetricBridge — architecture, detailed status

**Updated:** 12 Sep 2026 · **Verified against:** the initial import commit
**Summary view:** [`architecture.html`](architecture.html) · **Design record:** [`metricbridge-architecture-v0.html`](metricbridge-architecture-v0.html)

This document tracks what is actually built, how each piece was verified, what comes next and
where the project is headed. Every _completed_ claim cites code and the test that proves it.
Everything else is labelled design or expectation.

## Where we are

**Phase 0 — design — complete. Phase 1 — open-source core, proven correct — approved, not started.**
No implementation exists yet.

## Completed

### Phase 0 · Design and stack validation

**What was produced**

- Architecture v0 rev 2 (`docs/metricbridge-architecture-v0.html`): five principles (trust, discovery,
  refusals, guardrails, determinism), seven declined alternatives with revisit triggers, conformance
  strategy with a costed engine matrix, and a distribution strategy.
- A throwaway spike (`spike/`, 501 lines) that exercised the chosen stack.

**What was verified, and how**

| Finding | Evidence |
| --- | --- |
| mcp 2.2.0 renamed FastMCP to `MCPServer`; v1 snippets fail on import | spike import, recorded in `spike/README.md` |
| `MCPServer.run(transport=…)` supports stdio, sse, streamable-http | same |
| sqlglot 30.18.0 and duckdb 1.5.5 install and run together | `uv.lock` |

**What code review of the spike found** (12 Sep 2026, during Phase 1 planning)

The spike returns wrong answers silently in four ways: a snapshot metric summed with no grain,
dropped final-day rows on timestamp partitions, non-portable `DATE_TRUNC`, and non-portable
placeholders. Evidence and reproduction are in [`phases/phase-1.md` §1](phases/phase-1.md). These
findings shape Phase 1: its purpose is proof of correctness, not feature count.

**Divergences between design doc and code**

- Joins, lookback windows, ratio metrics, HAVING placement and AST guardrails are designed but not in the spike.
- The symmetry rule is violated in the spike (`order_by` and operator rules missing from `signature()`).
- `pyproject.toml` declares `src/metricbridge` and a `metricbridge` entry point that do not exist.

## In progress

The governance PR is open: open-source files, contribution stages, release train, protected `main`.
The Phase 1 plan was approved on 12 Sep 2026; milestone 1.0 is next.

## Next — Phase 1

Full plan: [`phases/phase-1.md`](phases/phase-1.md).

| Milestone | Delivers |
| --- | --- |
| 1.0 | Package skeleton, CI, branch protection, gates |
| 1.1 | Manifest model: joins, additivity, ratio and trailing metrics |
| 1.2 | Rule registry: validator and signature from one source |
| 1.3 | BM25 discovery; `discover_metrics`, `get_metric_signature` over MCP |
| 1.4 | Dialect-aware compiler |
| 1.5 | AST guardrails; adversarial refusal suite |
| 1.6 | DuckDB execution; `query_metric` |
| 1.7 | Conformance harness on DuckDB, Postgres, ClickHouse; parity matrix |
| 1.8 | Reference evaluator; 3M-request differential soak per engine |
| 1.9 | BigQuery and Snowflake |
| 1.10 | `uvx` demo, open-source hygiene, release gate, phase close |

**Release protection:** no pre-live branch. Nightly full soak on `main` blocks releases when red;
a final release ships only from a release candidate whose full suite passed. Releases follow a
monthly train ([`RELEASING.md`](../RELEASING.md)).

**Accuracy target:** zero tolerated mismatches, plus ≥ 3,000,000 consecutive correct
randomised requests per local engine (DuckDB, Postgres, ClickHouse). That bounds the failure rate
below 1 in 1,000,000 at 95% confidence. BigQuery and Snowflake are verified on every distinct SQL
shape the generator produces, because 3M cloud queries would take days and cost real money.

## Where we are headed

| Phase | Theme | Free / paid |
| --- | --- | --- |
| 1 | Open-source core, proven correct across engines | free |
| 2 | Real-world adoption: dbt and Cube adapters, BigQuery cost budgets, spend accounting | free |
| 3 | Shared deployment: streamable-http, auth, tenancy | free |
| 4 | Telemetry and governance: OpenTelemetry spans, usage census | free core |
| 5 | Org control plane: SSO/RBAC, budgets and chargeback, audit export | paid |

Phases 2–5 are direction, not plans. Each gets `/plan-phase` when its predecessor closes. The
open-core boundary is fixed from the design: correctness and guardrails are never paywalled.

## Decision log

| Date | Decision | Where |
| --- | --- | --- |
| 12 Sep 2026 | Python, MCP 2.x, sqlglot, DuckDB; agents never author SQL; BM25 before vectors; N:1 joins only | v0 rev 2 |
| 12 Sep 2026 | Repository public from day one under Apache-2.0; outside contributions closed until Phase 1 closes; all changes by PR with an owner-approved summary | `GOVERNANCE.md`, `CLAUDE.md` |
| 12 Sep 2026 | Open-source correctness before any paid tier | owner direction; `phases/phase-1.md` |
| 12 Sep 2026 | Phase 1 scope spans v0 phases 1–2 and §08 conformance; five engines incl. BigQuery and Snowflake; native YAML, dbt in Phase 2 | `phases/phase-1.md` D1, D3, D4 |
| 12 Sep 2026 | Accuracy = zero tolerated mismatches + ≥ 3M randomised requests per engine | `phases/phase-1.md` D2 |
| 12 Sep 2026 | No `ple` branch; protection sits on releases (nightly soak, green release candidate) | `phases/phase-1.md` D5; `CLAUDE.md` Releases |
| 12 Sep 2026 | Monthly release train; patch releases for correctness and security fixes only | `RELEASING.md` |
| 12 Sep 2026 | 3M claim covers local engines; BigQuery and Snowflake verified on every generated SQL shape | `phases/phase-1.md` E5, D6 |
