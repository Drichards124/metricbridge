# Changelog

All notable changes to MetricBridge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release cadence: [RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- Cumulative metrics compile: trailing windows and grain-to-date, as anchor periods joined to the
  rows their window covers. The scan is widened to cover the lookback, and `CompiledQuery` reports
  both the output window and the scan window.

- Ratio metrics compile to two aggregates divided after grouping, joined on every group key. A zero
  denominator yields null rather than zero.
- Snapshot metrics with a declared roll-up compile to one row per group per period, chosen by the
  declared window (`min` or `max`), then aggregated — month-end stock rather than a sum of days.

- SQL compilation for simple metrics: one parameterised statement per request, rendered for DuckDB,
  Postgres, BigQuery, Snowflake and ClickHouse. Values are always bound, never interpolated.
- Date ranges compile to half-open bounds (`>= start`, `< end + 1 day`), so the last day is not
  dropped on timestamp columns.
- Measures may declare `agg_time_dimension` with `partition_lag_days`, for tables partitioned on
  ingestion date but measured by a business date; both columns are bounded.
- Joins to dimensions are always LEFT, and each one reports its null-key row count in the result.

- MCP server (`metricbridge --manifest PATH`, stdio) exposing `discover_metrics` and
  `get_metric_signature`. Refusals come back as data (`ok: false` with structured errors), never as
  protocol errors.
- Lexical metric discovery (BM25) over names, descriptions, synonyms and authorised cuts, filtered
  by domain and tier. Refusal suggestions use the same ranking, with fuzzy matching for typos.
- When no metric matches confidently, `discover_metrics` asks the user which they mean (MCP
  elicitation) where the client supports it, and otherwise returns the catalog with `no_match`.
  Unanswerable phrasings are recorded so the missing synonym can be added to the manifest.
- Signatures state `time_grain_required`, so a metric that can only answer at its base grain says so.

- Manifest format: YAML semantic models (entities, dimensions, measures) and `simple`, `ratio` and
  `cumulative` metrics, following dbt MetricFlow's vocabulary. See [GLOSSARY.md](GLOSSARY.md).
- Requests are validated against a metric's contract before any SQL exists, and refused with a
  stable code, the offending field, a remediation and the authorised alternatives — all problems at
  once. See [docs/error-codes.md](docs/error-codes.md).
- `get_metric_signature` data: the exhaustive contract for one metric — authorised cuts (with their
  qualified names), supported grains, the mandatory date bound and its cap, filter operators,
  ordering and row limits.
- Measures take `additive: true|false`; a declared roll-up (`non_additive_dimension`) requires
  `additive: false`. Metrics take `replaced_by` for deprecations.
- Metrics can declare filters that are part of their definition and apply to every query, listed in
  the signature as `filters.always_applied`.
- Manifests are validated when loaded. Every problem is reported at once with its file and field
  path, including unknown keys, many-to-many joins, missing partition dimensions and features not
  supported in this version.
