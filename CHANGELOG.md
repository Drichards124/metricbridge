# Changelog

All notable changes to MetricBridge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release cadence: [RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- The server checks the manifest against the database before it starts, and refuses to start
  with every problem listed when:
  - a declared table cannot be read;
  - an entity, dimension or measure expression does not evaluate on its table;
  - a key that a join targets repeats a non-null value, which would silently inflate every measure
    joined through it (the count is reported, not the values);
  - the DuckDB file is named like a schema the manifest uses (`storefront.duckdb` with
    `storefront.<table>`), which makes every table reference ambiguous — rename the file.

  Uniqueness is checked at startup only: restart the server after reloading a dimension table.

- A refused manifest — invalid, or contradicted by the database — prints `metricbridge: ` and the
  problem list on stderr, without a Python traceback, and exits with code 1. Other startup failures
  still show their traceback.

- `query_metric` answers a governed metric on DuckDB: `metricbridge --manifest PATH --duckdb FILE`.
  The database is opened read-only, with file access disabled.
- Every answer states what is absent: `missing_periods` lists periods the data never produced,
  including a trailing-window period with no rows of its own, and `row_limit_reached` says rows
  were cut off, in which case `missing_periods` is `null`.
- `unreconciled` accounts, per joined entity, for the rows a join could not match — an empty key or
  a key naming a row that does not exist: `rows`, `empty_key_rows`, and the metric's `value` over
  just those rows. A ratio reports each half separately; a snapshot counts the rows it chose; a
  trailing metric counts its whole lookback, and `unreconciled_window` gives the dates covered.
  The totals are exact even when the row limit cuts the answer. `{}` means nothing was joined;
  `null` means no rows came back. A filter on a joined dimension removes unmatched rows before they
  are counted.
- `--statement-timeout SECONDS` (default 30) stops a long-running statement with
  `statement_timeout`. A result above the row ceiling is refused with `row_cap_exceeded` rather
  than truncated, and a database error returns `execution_failed` without the driver's message.
- Exact decimals are returned as strings, so money is never rounded through a float; periods are
  ISO dates.
- Results are always fully ordered — the requested order, then every group key — with NULL last on
  every engine, so the row limit keeps the same rows everywhere.

- Generated SQL is re-parsed and asserted before it can be executed: one SELECT, declared tables
  only, every scan bounded on a time column (including inside CTEs), no `SELECT *`, and a row limit
  within the ceiling. Failures return `guardrail_violation`.
- `SECURITY-THREAT-MODEL.md` documents the trust boundaries and what counts as a vulnerability.

- Cumulative metrics compile: trailing windows and grain-to-date, as anchor periods joined to the
  rows their window covers. The scan is widened to cover the lookback, and `CompiledQuery` reports
  both the output window and the scan window.

- Ratio metrics compile to two aggregates divided after grouping, joined on every group key. A zero
  denominator yields null rather than zero, and a group with a blank key — no channel, no known
  customer — keeps its ratio rather than reading as zero.
- Snapshot metrics with a declared roll-up count every row on the day the declared window (`min` or
  `max`) chooses, then aggregate them — month-end stock rather than a sum of days. The day is
  chosen per window grouping, so a product held in several warehouses on its last snapshot day
  contributes all of them; a requested cut such as `warehouse` groups the counted rows and does not
  change which day is chosen.

- SQL compilation for simple metrics: one parameterised statement per request, rendered for DuckDB,
  Postgres, BigQuery, Snowflake and ClickHouse. Values are always bound, never interpolated.
- Date ranges compile to half-open bounds (`>= start`, `< end + 1 day`), so the last day is not
  dropped on timestamp columns.
- Measures may declare `agg_time_dimension` with `partition_lag_days`, for tables partitioned on
  ingestion date but measured by a business date; both columns are bounded.
- Joins to dimensions are always LEFT, and each one reports its null-key row count in the result.

- MCP server (`metricbridge --manifest PATH --duckdb FILE`, stdio) exposing `discover_metrics`,
  `get_metric_signature` and `query_metric`. Refusals come back as data (`ok: false` with structured errors), never as
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
