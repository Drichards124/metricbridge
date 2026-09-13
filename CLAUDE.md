# MetricBridge — CLAUDE.md

A deterministic semantic firewall between autonomous agents and data warehouses. Agents request
governed metrics in structured form; MetricBridge compiles, guards and executes the SQL.

| Document | Role |
| --- | --- |
| `docs/phases/phase-<n>.md` | Phase plans, written with `/plan-phase <n>` |
| `docs/metricbridge-architecture-v0.html` | Frozen design record (v0 rev 2). Superseded by a new version, never edited |

The owner stays close to this project and sets its direction and pace. The rules below exist so
they can ask questions **before** work lands.

## Ground truth

Code and command output are the source of truth; docs are claims about them. Before writing any
status — a plan, a PR summary, an architecture doc — read the code or run the command, and cite
the path or output. Label anything unmeasured an _expectation_.

## Branches

- `main` is the only long-lived branch, and it is protected. Every change arrives by PR from a
  `<type>/<slug>` branch; the owner merges. There is no `ple`: MetricBridge ships as a package, so
  users get releases, not `main`, and the soak happens at release (see Releases).
- `spike/` is throwaway code that validated the stack. The implementation lives in
  `src/metricbridge/` and never imports from `spike/`.

## Gates

The gates are the steps of the `gates` job in `.github/workflows/ci.yml` — the single source of
truth, and a required check on `main`. Run each step locally before posting a pre-PR summary.
Always pass `--locked`: a dependency change updates `uv.lock` in the same PR (`uv lock`), and a
drifted lock fails the gate rather than silently resolving something new.

`uv sync` succeeds even when the declared package directory is missing, so an import test —
not a clean sync — is what proves the package is wired.

## Phases

- Plan each phase with `/plan-phase <n>`. Implementation starts only after the owner approves the plan.
- Each plan milestone becomes a GitHub issue; its PR closes it.

## Releases

Cadence, gates and who may release: [`RELEASING.md`](RELEASING.md) — a monthly release train, with
out-of-schedule patch releases for correctness and security fixes only. A red nightly run blocks the
train. A PR that changes user-visible behaviour updates `CHANGELOG.md`.

## Pre-PR summary — before every PR

Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md) and post it in chat,
then **wait for the owner's explicit approval**. Answer their questions and revise until approved;
then open the PR with the approved summary as its body. The summary is what gets approved, not the
diff. Keep it readable in five minutes and link to detail.

## Phase close

In the PR that closes a phase:

1. Every _completed_ claim in the phase plan and `CHANGELOG.md` is re-verified against the code and
   the tests that prove it.
2. A design change gets a new versioned design document in `docs/`; prior versions stay readable.
3. The maintainer's status briefings are regenerated from the code. They live outside this
   repository; public progress is the phase's GitHub milestone, its issues, and `CHANGELOG.md`.

## Correctness rules

These carry the design's core guarantees; a change that bends one needs the owner's approval first.

- Agents supply structured requests only. SQL text is always compiler-generated; values are always
  bound parameters.
- A conformance divergence closes by fixing the compiler or adding a documented normalisation
  rule. The expected value stays as the oracle computed it.
- An engine is _supported_ only while its parity matrix is green; otherwise it is labelled experimental.
- Every constraint the validator enforces appears in `get_metric_signature` (the symmetry rule).
- Telemetry records what was asked and what it cost, never result values. MetricBridge offers no
  raw-SQL execution path.

## Owner-only actions

Merging to `main`, creating release tags, publishing packages, creating cloud accounts or
credentials, spending money, changing repository settings, and moving the contribution stage in
[`GOVERNANCE.md`](GOVERNANCE.md).
