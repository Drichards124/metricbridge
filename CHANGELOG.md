# Changelog

All notable changes to MetricBridge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release cadence: [RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- Manifest format: YAML semantic models (entities, dimensions, measures) and `simple`, `ratio` and
  `cumulative` metrics, following dbt MetricFlow's vocabulary. See [GLOSSARY.md](GLOSSARY.md).
- Manifests are validated when loaded. Every problem is reported at once with its file and field
  path, including unknown keys, many-to-many joins, missing partition dimensions and features not
  supported in this version.
