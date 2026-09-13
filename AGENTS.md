# MetricBridge — agent and contributor guide

A deterministic semantic firewall between autonomous agents and data warehouses. Agents request
governed metrics in structured form; MetricBridge compiles, guards and executes the SQL.

## Note for AI agents

If you are acting for someone who is not a maintainer of this repository, read
[`CONTRIBUTING.md`](CONTRIBUTING.md) first: outside issues and pull requests are not accepted yet.

## Where things live

| Path | Role |
| --- | --- |
| `src/metricbridge/` | The implementation |
| `tests/` | The test suite |
| `spike/` | Throwaway code that validated the stack. The implementation never imports from it |
| `docs/phases/phase-<n>.md` | Approved phase plans. Implementation follows the current one |
| `docs/metricbridge-architecture-v0.html` | Frozen design record (v0 rev 2). Superseded by a new version, never edited |
| `RELEASING.md` · `GOVERNANCE.md` | Release train and gates · decision-making and contribution stages |

## Ground truth

Code and command output are the source of truth; docs are claims about them. Before writing any
status — a plan, a PR description, a changelog entry — read the code or run the command, and cite
the path or output. Label anything unmeasured an _expectation_.

## Branches and pull requests

- `main` is the only long-lived branch, and it is protected. Every change arrives by squash-merged
  PR from a `<type>/<slug>` branch. There is no pre-live branch: users get releases, not `main`.
- The PR description fills in [`.github/pull_request_template.md`](.github/pull_request_template.md).
- Each plan milestone is a GitHub issue; its PR closes it.
- A PR that changes user-visible behaviour updates `CHANGELOG.md`.

## Gates

`make check` runs every gate: lint, format check, and tests on the lowest and latest supported
Python. CI runs the same Makefile targets as the required `gates` check, so the `Makefile` is the
single source of the commands. Run `make check` before pushing; `pre-commit` runs the fast subset
on each commit (install once with `uvx pre-commit install`).

- Every `uv` command passes `--locked`. A dependency change updates `uv.lock` in the same PR
  (`uv lock`), and a drifted lock fails the gate rather than silently resolving something new.
- `uv sync` succeeds even when the declared package directory is missing, so an import test —
  not a clean sync — is what proves the package is wired.

## Releases

Cadence, gates and who may release: [`RELEASING.md`](RELEASING.md) — a monthly release train, with
out-of-schedule patch releases for correctness and security fixes only. A red nightly run blocks the
train.

## Correctness rules

These carry the design's core guarantees; a change that bends one needs maintainer approval first.

- Agents supply structured requests only. SQL text is always compiler-generated; values are always
  bound parameters.
- A conformance divergence closes by fixing the compiler or adding a documented normalisation
  rule. The expected value stays as the oracle computed it.
- An engine is _supported_ only while its parity matrix is green; otherwise it is labelled experimental.
- Every constraint the validator enforces appears in `get_metric_signature` (the symmetry rule).
- Telemetry records what was asked and what it cost, never result values. MetricBridge offers no
  raw-SQL execution path.
