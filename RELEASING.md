# Releasing

Users get releases, not `main`. The release is therefore the most heavily gated step.

## Versioning

[Semantic Versioning](https://semver.org). While the version is `0.x`, a minor release may change
behaviour; every such change is listed under **Changed** in the changelog with its migration step.
From `1.0.0`, breaking changes wait for a major release.

## Schedule — a monthly release train

| When | What |
| --- | --- |
| First Monday of the month | Release candidate `vX.Y.0rc1` tagged from `main`, if the latest nightly run is green |
| Following Monday | Final `vX.Y.0`, if the candidate's full suite is green and no regression was reported |

- A train with no user-visible change is skipped and noted in the changelog.
- A red candidate is fixed and re-cut as `rc2`; the final release slips rather than ships red.
- **Patch releases** `vX.Y.Z` are out of schedule and reserved for correctness and security fixes.
- The first train runs after Phase 1 milestone 1.10.

## Gates

| Stage | Runs |
| --- | --- |
| Pull request → `main` | Lint, unit, golden SQL, refusal suite, 10,000 randomised requests, conformance on DuckDB, Postgres and ClickHouse |
| Nightly on `main` | 3,000,000 randomised requests per local engine; BigQuery (nightly) and Snowflake (weekly) conformance over every generated SQL shape. A red run opens an issue and blocks the train. |
| Release candidate | The full nightly suite on the tagged commit, plus a clean-machine `uvx` install |
| Final release | Publishes only from a candidate whose full suite passed |

## Who can release

- Only the maintainer can create `v*` tags (repository ruleset).
- Publishing to PyPI uses trusted publishing from a protected `pypi` environment that requires the
  maintainer's approval.

## Changelog

[`CHANGELOG.md`](CHANGELOG.md) follows [Keep a Changelog](https://keepachangelog.com). Entries
describe observable behaviour — tools, arguments, error codes, supported engines, fixes users would
notice — never internal refactors.
