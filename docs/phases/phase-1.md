# Phase 1 — Open-source core, proven correct across engines

**Status:** approved · 12 Sep 2026
**Written with:** `/plan-phase 1`

## 1 · Ground truth

What exists today, verified against the code in the initial import commit, not the design doc.

| Component | Current state | Evidence |
| --- | --- | --- |
| Implementation | None. `pyproject.toml` packages `src/metricbridge` and exposes `metricbridge.server:main`; neither exists, so the package cannot build or run. | `pyproject.toml:19,26`; no `src/` directory |
| Tests / CI / gates | None. `pyproject.toml` points pytest at `tests/`, which does not exist. No `.github/`. | repo tree |
| Spike | 501 lines across `manifest.py`, `index.py`, `contract.py`, `compile.py`, `errors.py`. Unreviewed, untested, single-table metrics only. | `spike/README.md` |
| Joins, lookback windows, ratio metrics, HAVING, AST guardrails | Designed (v0 §04, declined #5), **absent from the spike**. | `spike/manifest.py:32` — a metric has one `table`, no joins |
| Symmetry rule (§02) | **Already violated in the spike.** `validate()` enforces an `order_by` rule and a filter-operator set that `signature()` never reports. | `spike/contract.py:33` (operators) and `:155-163` (order_by) vs `signature()` at `:52-80` |

### Correctness defects found in the spike

Each of these returns a wrong number or broken SQL with **no error**. They are why this phase is
about proof, not features.

1. **A snapshot metric summed across time with no grain is not refused.** The non-additive check
   only fires when `time_grain` is set and not `day`. Request `inventory_on_hand` for a 90-day
   range with no grain and the compiler emits `SUM` over 90 daily snapshots: 90× the real stock.
   _Evidence: `spike/contract.py:130` (`if not metric.additive and request.time_grain and …`), by reading._
2. **Inclusive date bound drops the last day on timestamp columns.** `BETWEEN '2026-09-01' AND
   '2026-09-30'` against a `TIMESTAMP` column excludes everything after midnight on the 30th.
   _Evidence: DuckDB 1.5.5 probe — two rows on 30 Sep (00:00 and 10:00) summed to `1`, not `2`._
3. **Time bucketing is not portable.** `DATE_TRUNC` is built as an opaque function, so sqlglot
   emits `DATE_TRUNC('month', col)` verbatim for BigQuery, whose signature is
   `DATE_TRUNC(col, MONTH)`. _Evidence: sqlglot 30.18.0 probe, `bigquery` dialect output._
4. **Parameter placeholders are not portable.** The same statement renders `?` (DuckDB), `%s`
   (Postgres) and `{?: }` (ClickHouse, not valid). _Evidence: same probe._

## 2 · Goal and exit criteria

**Goal:** an open-source MetricBridge that an analytics engineer can run locally over MCP, whose
every answer is identical to an independently computed correct answer on every supported engine,
and whose every unsafe request is refused with a fix.

### What "99.9999% accurate" means here

A deterministic compiler is either right or wrong for a given request, so accuracy is defined as
**the observed failure rate over a large randomised sample, with a statistical upper bound**:

- **Zero** mismatches are tolerated. One mismatch is a bug, fixed and turned into a permanent test.
- By the rule of three, **3,000,000 consecutive correct results** bound the true failure rate
  below 1 in 1,000,000 (99.9999%) at 95% confidence. The claim is published per engine, with the
  sample size, seed range and date.
- "Correct" means **equal to the reference evaluator** — a separate Python implementation of the
  metric semantics that shares no code with the compiler — and equal across engines.

### Exit criteria

| # | Criterion | Measured by |
| --- | --- | --- |
| E1 | CI runs lint + unit + golden suites on every PR; `main` requires it green | GitHub branch protection + Actions |
| E2 | Every enforced constraint appears in the signature | symmetry test enumerating the rule registry |
| E3 | Every adversarial case is refused with a stable code and remediation | refusal suite (≥ 40 cases), 100% pass |
| E4 | Hand-written reference SQL matches MetricBridge on every conformance case | conformance suite (≥ 60 cases) on DuckDB, Postgres, ClickHouse |
| E5 | ≥ 3,000,000 randomised requests per local engine (DuckDB, Postgres, ClickHouse), zero mismatches vs reference evaluator; BigQuery and Snowflake agree on every distinct SQL shape the generator produces | nightly differential soak, results published |
| E6 | AST guardrails reject every mutated unsafe statement | guardrail mutation suite, 100% |
| E7 | Parity matrix and divergence catalog generated from the latest run | `docs/conformance/` artifacts, regenerated in CI |
| E8 | Cold `uvx metricbridge demo` reaches the first refusal and first answer in < 60 s | timed script in CI |
| E9 | Phase plan, changelog and maintainer briefings re-verified against the code at phase close | phase-close PR |
| E10 | A release is published only from a commit whose full suite passed on the tagged candidate | release workflow refuses a red candidate |
| E11 | Contract invariants hold over generated manifests and requests, not just fixtures | property suite (Hypothesis) on every PR from 1.3 |
| E12 | Every silent-wrong-answer class in `docs/failure-modes.md` is guarded by a named test or recorded as unguarded | phase-close audit |

## 3 · Scope

**In:** native YAML manifest shaped after MetricFlow (D7); N:1 / 1:1 joins; simple, ratio and trailing-window metrics;
additivity; the three MCP tools over stdio; compiler; AST guardrails; statement timeout and row
cap; DuckDB, Postgres and ClickHouse (local, $0); BigQuery and Snowflake (cloud, ~$0–5 a month);
conformance suite, release gate, reference evaluator,
differential soak; parity matrix; `uvx` demo; open-source hygiene files.

**Out, and where it goes:**

- dbt / Cube adapters → Phase 2 (they are readers into the model this phase proves; proving the model first means adapter bugs cannot hide compiler bugs).
- BigQuery dry-run cost budgets, cumulative spend accounting → Phase 2.
- Documentation site → Phase 2.
- `derived` and `conversion` metrics, `percentile` / `median` / `sum_boolean` aggregations, sub-day granularities, `natural` entities → Phase 2 (D9).
- streamable-http, auth, multi-tenancy, telemetry, org control plane, anything paid → later phases.
- Opening outside contributions → after Phase 1 closes, in stages ([`GOVERNANCE.md`](../../GOVERNANCE.md)).

## 4 · Milestones

Each is one PR unless noted. Verification is written first and must be seen failing.

### 1.0 · Foundations
Package skeleton at `src/metricbridge`, `tests/`, ruff, pytest, GitHub Actions CI added as a
required check on the `main` ruleset, Dependabot for Actions and uv, a `Makefile` whose
`make check` is the single entry point for every gate (CI calls the same targets), pre-commit hooks,
zizmor scanning of workflows, CodeQL default setup, and `AGENTS.md` as the tool-neutral agent
guide that `CLAUDE.md` imports. The spike stays read-only for reference.
**Verify:** a deliberately failing test turns the PR check red and blocks merge; removed, it goes green.

### 1.1 · Manifest model
A two-layer model shaped after MetricFlow (D7): semantic models (one per table) with typed entities,
dimensions and measures, and `simple`, `ratio` and `cumulative` metrics that reference measures.
Joins are derived from entity types, so only N:1 and 1:1 paths exist. Snapshot measures declare how
they roll up (D8). Deferred elements are refused by name (D9). The manifest version is a content
hash. Load-time validation collects every problem with its file and field path: unknown keys,
duplicates, unknown references, N:M paths, partition rules, snapshot declarations, windows. Column
references are checked against the warehouse when an engine is attached (1.6b).
**Verify:** one invalid-manifest fixture per error class, each rejected with the expected message.
`GLOSSARY.md` defines the model's terms (metric, dimension, grain, additivity, join cardinality) as
they land.

### 1.2 · Rule registry, validator and signature
Each rule declares its check **and** its signature entry in one place, so the symmetry rule holds by
construction instead of by checklist. Fixes spike defect 1: a snapshot measure with no declared
rollup is refused when a query would sum it across time; a declared rollup is admitted (D8). Error codes versioned.
**Verify:** symmetry test fails when a rule is registered without a signature entry; defect-1 case
(`inventory_on_hand`, 90 days, no grain) goes red → refused.
**Parallel registration sites:** removes one — validator and signature collapse into the registry.

### 1.2b · Reality check against public semantic manifests
A **feature census**, not a load. MetricFlow's fixture manifests are written in dbt's own YAML
(`semantic_model:` wrappers, `node_relation`, nested `type_params`), so our loader would reject all
of them on shape and tell us nothing. The question worth answering is whether our *model* can
express what real semantic layers express, so the census counts the modelling features those
manifests use — metric types, aggregations, entity types, snapshot roll-ups, aggregation time
dimensions, multi-hop joins, offsets, SCD — and maps each to our status: supported, deferred (D9),
or an unmodelled gap. Fetched at a pinned commit by sparse shallow clone, never vendored.
Publishes `docs/conformance/manifest-coverage.md`.
**Verify:** feature extraction is unit-tested against local fixtures, so PR gates need no network;
every gap the census finds becomes an issue or a plan change.

### 1.2c · Metric-level filters
A certified definition often *is* a filter — "revenue" means amount excluding refunds and trials.
The census found 16 uses across the public corpus, and without this people cannot express their
real definitions in a MetricBridge manifest (D14). Declared structurally, like a request filter
(`field`, `operator`, `value`), never as raw SQL: the compiler builds the syntax and the driver
carries the values. The signature advertises it, so an agent can see what a metric permanently
excludes.
**Verify:** a metric with a declared filter compiles it into every query; the filter appears in the
signature; a filter naming an unauthorised field is refused at load.

### 1.3 · Discovery and the MCP read tools
BM25 index, `discover_metrics` and `get_metric_signature` on `MCPServer` (mcp 2.x) over stdio.
Dimension suggestions in refusals are ranked by the same index (names, descriptions, synonyms),
replacing near-name matching (D13). Property-based tests start here (D10): the first invariant is
that `validate` accepts exactly what the signature advertises, over generated manifests.
**Verify:** golden search results for ≥ 30 phrasings; an MCP client test lists exactly three tools and round-trips the two read tools.

### 1.3b · Asking instead of guessing
When discovery cannot answer confidently, the better move is a question, not a better guess (D15).
Where the client supports MCP elicitation, `discover_metrics` asks the user which metric they mean,
with the candidates as the schema; where it does not, the structured `no_match` reply delivered in
1.3 carries the catalog and tells the agent to ask. Unmatched phrasings are recorded, because each
one is either a missing synonym (a deterministic fix) or evidence toward the recall failure that
would justify embeddings.
**Verify:** a test client that answers an elicitation drives the flow end to end; a client without
the capability still gets the `no_match` catalog reply.

### 1.4 · Compiler
Split into **1.4a** (core: simple metrics, N:1 LEFT joins, WHERE/HAVING placement, time bucketing,
half-open date bounds, injected row limit, per-dialect rendering, bound parameters) and **1.4b**
(ratio CTEs and snapshot `window_choice`) and **1.4c** (cumulative windows with lookback widening)
— one PR each, the last closing #6 (D16). Delivered 13 Sep 2026. Cumulative is split off because portable trailing windows
need either a non-portable `RANGE INTERVAL` frame or an explicit period join, and that decision
deserves its own review.
Dialect-aware compilation through sqlglot typed expressions: half-open date intervals
`[start, end + 1 day)`, dialect-correct time bucketing, an aggregation time dimension that may
differ from the partition column (both bounded, D11), `count_distinct` and `average` recomputed from
base rows rather than rolled up (D11), LEFT fact-to-dimension joins, CTEs for ratio and
trailing metrics with widened scan windows, snapshot measures resolved to their declared window
choice per period (D8), WHERE / HAVING placement, injected row limit,
per-driver parameter style. Fixes spike defects 2–4.
**Verify:** golden SQL snapshots per dialect; defect 2–4 probes as failing tests first.

### 1.5 · AST guardrails and refusal suite
Independent re-parse of generated SQL: single SELECT, every scan bounded (including CTE legs), only
declared tables, no `SELECT *`, limit within ceiling. Adversarial refusal suite.
**Verify:** mutation suite — take valid compiled SQL, remove a predicate / add a table / stack a
statement, assert each mutant is rejected (E6). Refusal suite (E3).
`SECURITY-THREAT-MODEL.md`: roles, trust boundaries (agent → gateway → warehouse), and which
failures count as vulnerabilities — including silently wrong results, which comparable projects
class as ordinary correctness bugs.

### 1.6 · Execution and `query_metric` on DuckDB
Engine adapter interface, DuckDB adapter, statement timeout, row cap, null-join-key counts in the
result, `query_metric` tool, result normalisation (column case, types, NULL ordering).
**Verify:** end-to-end MCP test on a seeded dataset; timeout test with a deliberately slow query.

### 1.6b · Warehouse verification
With an engine attached, check the manifest against the warehouse it describes: every declared
column exists, and every key declared unique is unique in the data — otherwise an "N:1" join fans
out while the manifest says it cannot. Split from 1.6 because both need introspection queries and
bring their own failure modes.
**Verify:** a manifest naming a missing column is refused at startup; a seeded duplicate key that
a join targets is refused at startup.

### 1.6c · Unreconciled entity accounting
Replace `null_key_rows` with `unreconciled`: per joined entity, the rows and the metric value that
match no row in the joined table — an empty key or a key with no match — computed over the whole
result before the limit applies, so a truncated answer still carries exact totals. Covers every
metric shape, including ratio and cumulative, and `count_distinct` / `average`, whose unmatched
value cannot be summed from groups. A compiler feature, kept apart from 1.6's execution runtime.
**Verify:** an orphan key (`customer_id` with no customer) is counted; totals are identical with
and without a row limit; golden SQL per dialect, executed on DuckDB, with other engines confirmed
in 1.7.

### 1.7 · Conformance harness — DuckDB, Postgres, ClickHouse
Corpus: TPC-H SF1 generated by DuckDB and exported to Parquet once, plus the four purpose-built
catalogs (storefront, subscriptions, inventory, marketplace) as native YAML. `local-data-warehouses/`
holds one Docker Compose setup per engine, run through `make test-<engine>` targets. Hand-written reference SQL per case. Parity matrix and divergence catalog
generated into `docs/conformance/`. Likely two PRs: harness + DuckDB, then the two Docker engines.
**Verify:** E4 and E7. Every divergence found becomes a normalisation rule with its own test.
The corpus includes fan-trap and chasm-trap shapes — two facts joined through a shared dimension —
which must be refused or computed correctly, never quietly fanned out (D11).

### 1.8 · Reference evaluator and differential soak
Pure-Python reference evaluator; seeded random request generator over the corpus (valid and invalid
requests); comparison of all engines against the evaluator and each other. PR CI runs 10,000 cases;
a nightly workflow runs the 3,000,000-case soak as one job per local engine (each under GitHub's
6-hour job limit; Actions minutes are free on a public repository) and publishes the result to
`docs/conformance/`. The generator also emits its set of distinct SQL shapes for 1.9.
**Verify:** seed a known compiler bug on a branch and confirm the soak finds it; then E5. A red
nightly run opens an issue and blocks releases until fixed or reverted.
_Expectation, unmeasured: small-data DuckDB queries at ~1 ms put a 3M soak near an hour per engine;
Postgres and ClickHouse slower. Measured in 1.8 before committing to nightly._

### 1.9 · Cloud engines — BigQuery and Snowflake
Adapters, secrets in GitHub Actions, the conformance suite plus every distinct SQL shape from
1.8's generator — BigQuery nightly within its free tier, Snowflake weekly in a single warehouse
session. No 3M soak here: at cloud round-trip latency it would run for days and cost roughly $200 a
run (expectation, from ~100 ms per query).
**Owner:** create the GCP project and Snowflake trial, add credentials as repo secrets. Start the
Snowflake 30-day trial only when 1.8 is merged.

### 1.10 · Open-source readiness and phase close
`uvx metricbridge demo` (refusal first) with its manifests in `examples/`, README usage section and parity matrix, first release
train per [`RELEASING.md`](../../RELEASING.md). Release workflow: a `vX.Y.ZrcN` tag runs the full suite on
the tagged commit and a clean-machine install, then publishes a PyPI pre-release; a final tag
publishes only from a green candidate. Maintainer briefings regenerated from the code.
**Verify:** E8, E9, E10 — a deliberately red candidate is refused publication.
**Owner:** create the PyPI project and approve trusted publishing; the first publish is yours.

## 5 · Risks

- **Reference evaluator shares a misconception with the compiler.** Mitigated by hand-written reference SQL (E4) written from the metric definition, not from either implementation.
- **Randomised soak is only as good as its generator.** Coverage report on the generator: every rule, grain, join and operator must appear in the sample.
- **Docker engines differ from their managed cloud versions** (ClickHouse Cloud, Postgres flavours). Recorded in the parity matrix as the exact version tested.
- **mcp 2.x is young.** Pin exact versions; the rename that broke FastMCP will recur.

## 6 · Decisions — all decided by the owner, 12 Sep 2026

- **D1 · Phase 1 scope.** The v0 doc calls "Phase 1" discovery only, with no SQL. Your ask is a validated open-source core across multiple databases, which spans v0 phases 1–2 plus the §08 conformance work. _Decided:_ your reading. This plan renumbers accordingly, and the v0 roadmap is superseded by this plan.
- **D2 · Accuracy definition.** _Decided:_ zero tolerated mismatches, plus a published statistical bound from ≥ 3M randomised requests per engine (§2). The alternative is a fixed case count with no statistical claim.
- **D3 · Cloud engines in Phase 1.** (a) include BigQuery + Snowflake as 1.9, (b) move them to Phase 2 and ship Phase 1 on three local engines. _Decided:_ (a). Your wedge audience runs Snowflake and BigQuery, and running both costs about $0–5 a month.
- **D4 · Manifest format.** _Decided:_ native YAML for Phase 1, with the dbt adapter in Phase 2 validated against these same suites.
- **D5 · Branching and release protection.** _Decided:_ no `ple` branch. `main` is the only long-lived branch; protection sits on releases (nightly full soak blocks releases when red; release candidates must pass the full suite before a final release). Compiler PRs run the 10k-case soak pre-merge; the 3M soak runs nightly.
- **D6 · Public from day one.** _Decided:_ the repository is public under Apache-2.0 from its first commit, so GitHub Actions minutes are free and `main` and release tags are protected by rulesets. Outside issues and pull requests stay closed until Phase 1 closes (`GOVERNANCE.md`). Releases follow a monthly train (`RELEASING.md`). The 3M claim covers the local engines; BigQuery and Snowflake are verified on every generated SQL shape (E5). No self-hosted or Fly runners.
- **D7 · Manifest shape.** _Decided:_ follow dbt MetricFlow's two layers — semantic models with typed entities, dimensions and measures; metrics referencing measures — rather than the spike's metric-per-table. Joins derive from entity types, so N:1 / 1:1 hold by construction and N:M is refused at load. The Phase 2 dbt adapter becomes a mapping, not a translation. MetricBridge adds `tier`, `owner`, `synonyms`, `max_window_days` and a content-hash version.
- **D8 · Snapshot measures.** _Decided:_ a snapshot measure that declares `non_additive_dimension` (window choice `min` or `max`) is answered with the value at that point in each period — for example month-end inventory. A snapshot measure without the declaration is refused when a query would sum it across time. This supersedes the v0 design's always-refuse position (§03) for declared rollups; the v1 design document at phase close records it.
- **D10 · Property-based tests, from 1.3.** _Decided:_ every milestone ships Hypothesis property tests for its own invariants, over *generated* manifests and requests. The grain bug found on 13 Sep — a weekly table would have answered a daily question — existed because every fixture was daily-partitioned and the symmetry test only checked those fixtures. The first invariant is "accepted ⟺ advertised": a grain, cut or filter is accepted exactly when the signature offers it.
- **D11 · Modelling gaps in Phase 1.** _Decided:_ 1.4 takes the two silent-wrong-number paths common in real warehouses — an aggregation time dimension that differs from the partition column, and `count_distinct` / `average` recomputed from base rows rather than rolled up. 1.7's corpus adds fan-trap and chasm-trap shapes. SCD type 2 (`natural` entities), timezones and multi-currency each need their own design and go to Phase 2.
- **D12 · Reality check without a private project.** _Decided:_ no real dbt project is available to borrow, so milestone 1.2b takes a feature census of MetricFlow's public semantic-manifest fixtures (Apache-2.0) at a pinned commit and publishes `docs/conformance/manifest-coverage.md`. A census rather than a load: those fixtures use dbt's YAML shape, so loading them would measure format translation, not modelling coverage. The gaps it finds drive plan changes.
- **D14 · Census findings (13 Sep 2026).** _Decided:_ `derived` metrics stay in Phase 2 despite being the most-used type in the corpus (63 uses) — they are arithmetic over metrics Phase 1 already computes correctly, the fixture corpus overstates their real frequency, and 1.4 is already the largest milestone. Metric-level filters (16 uses) come into Phase 1 as milestone 1.2c, because without them a manifest cannot state what a certified metric actually means. The census also found an interop bug of ours — declared values must be matched case-insensitively — now fixed.
- **D16 · Compiler shape (13 Sep 2026).** _Decided:_ the compiler is split 1.4a / 1.4b so each PR is reviewable. A measure whose aggregation time dimension differs from the partition column must declare `partition_lag_days`; the compiler then bounds the business date exactly and the partition widened by that lag, and a manifest declaring one without the other is refused. Values bind as **named** parameters rendered per dialect (ClickHouse needs a type annotation sqlglot omits — the adapter supplies it in 1.7). A ratio with a zero denominator yields null, not zero. Null join keys are counted in the same statement rather than a second round trip.
- **D15 · Clarify rather than infer (13 Sep 2026).** _Decided:_ when a question's words appear nowhere in the catalog, MetricBridge asks instead of guessing. Discovery returns a `no_match` refusal carrying the catalog, flags weak matches as not confident, and the tool description tells the agent to ask the user rather than rephrase. Milestone 1.3b adds MCP elicitation so the question reaches a person where the client supports it. Embeddings stay deferred: a similarity score cannot tell revenue from order count when someone says "sell", and each clarification instead becomes a synonym that fixes the question deterministically for everyone after.
- **D13 · Suggestion ranking.** _Decided:_ refusal alternatives are ordered by the join graph now (own cuts first, then per entity) and capped at 25; 1.3 ranks them with the BM25 index built for discovery; embeddings stay deferred until telemetry shows refusals that are not repaired in one turn.
- **D9 · Phase 1 subset.** _Decided:_ `derived` and `conversion` metrics, `percentile`, `median` and `sum_boolean` aggregations, sub-day granularities and `natural` entities are refused at load as "not supported in this version", never ignored.

## 7 · Revision log

- 12 Sep 2026 — proposed.
- 12 Sep 2026 — approved: D1–D4 as recommended; D5 (no `ple`, release gate) added; E10 added; 1.8, 1.9, 1.10 updated.
- 12 Sep 2026 — D6 added (public from day one, contributions closed, monthly release train); E5 split into local-engine soak and cloud shape coverage; 1.0, 1.8, 1.9, 1.10 updated.
- 12 Sep 2026 — status briefings moved out of the repository; E9 and 1.10 updated.
- 12 Sep 2026 — structure aligned with comparable projects (MCP Python SDK, MetricFlow, sqlglot, Iceberg-Python, Pydantic): 1.0 adds `AGENTS.md`, `Makefile`, pre-commit, zizmor, CodeQL; `GLOSSARY.md` → 1.1, `SECURITY-THREAT-MODEL.md` → 1.5, `local-data-warehouses/` → 1.7, `examples/` → 1.10, documentation site → Phase 2; issue forms and a code of conduct arrive with contribution stage 1.
- 12 Sep 2026 — D7 (MetricFlow-shaped manifest), D8 (declared snapshot rollups), D9 (Phase 1 subset); 1.1, 1.2, 1.4 and scope updated.
- 13 Sep 2026 — D15 added (clarify rather than infer); new milestone 1.3b (elicitation).
- 13 Sep 2026 — D10–D13 added; new milestone 1.2b; E11 and E12 added; 1.3, 1.4 and 1.7 updated; `docs/failure-modes.md` created.
- 13 Sep 2026 — 1.2b run: D14 added (derived deferred, metric-level filters into Phase 1 as new milestone 1.2c); case-insensitive value matching fixed.
- 13 Sep 2026 — 1.6 design agreed: DuckDB has no statement-timeout or row-cap setting, so the adapter enforces both; the row cap refuses rather than truncates; driver error text is neither returned nor logged; results are totally ordered with NULL last; ratio and cumulative answers report null-key counts as `null` until counted. Warehouse verification (column references, key uniqueness) split into new milestone 1.6b.
- 13 Sep 2026 — 1.6 review found `null_key_rows` misses orphan keys and is lost to the row limit; pre-limit unreconciled accounting for every shape deferred to new milestone 1.6c, and both gaps recorded in `docs/failure-modes.md`.
- 13 Sep 2026 — 1.6b design agreed: a warehouse that contradicts the manifest stops the server at startup — unreadable tables, expressions that do not evaluate, and repeated non-null values in keys a join targets (keys nothing joins to are not scanned). The uniqueness count is the one full-table scan, allowed because it is built from the manifest before any request; security goal 3 reworded to say so. A DuckDB file named like a manifest schema gets its own message.
- 14 Sep 2026 — 1.6c design agreed: one totals CTE over the same scan for every shape, carried on every row so the limit cannot change it; unmatched tested on the joined table's key; per entity `rows`, `empty_key_rows` and `value`, with a ratio reporting each half separately; a trailing metric counts its whole lookback and reports `unreconciled_window`; an answer with no rows reports `null`. Filters on joined dimensions dropping unmatched rows recorded as unguarded in `docs/failure-modes.md`, for the 1.7 corpus.
