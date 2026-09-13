# Changelog

All notable changes to MetricBridge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release cadence: [RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

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
- Manifests are validated when loaded. Every problem is reported at once with its file and field
  path, including unknown keys, many-to-many joins, missing partition dimensions and features not
  supported in this version.
