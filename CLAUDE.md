# MetricBridge — CLAUDE.md

A deterministic semantic firewall between autonomous agents and data warehouses. Agents request
governed metrics in structured form; MetricBridge compiles, guards and executes the SQL.

| Document | Role |
| --- | --- |
| `docs/architecture.html` | Living summary — what it is, completed, in progress, next |
| `docs/architecture-detailed.md` | Living detail — what was built and how it was verified, what's next, direction |
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

None exist yet; phase 1 milestone 1.0 creates them and records the exact commands here. Until
then, every PR summary says plainly that no automated gate ran.

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

## Architecture docs — at every phase close

In the PR that closes a phase (and in any PR that changes the architecture):

1. **`docs/architecture.html`** — keep it a summary: what MetricBridge is, status per phase
   (completed / in progress / next), engines and their parity status, next steps.
2. **`docs/architecture-detailed.md`** — completed work in detail (what was built, where it lives,
   how it was verified, divergences found), what is next, where the project is headed, decision log.
3. Every _completed_ claim cites the code path and the test that proves it, re-run before writing.
4. A design change gets a new versioned design document; prior versions stay readable.

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
