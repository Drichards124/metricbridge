# Governance

## Maintainer

MetricBridge has a single maintainer, [@Drichards124](https://github.com/Drichards124), who sets
direction, approves plans and merges every change.

## How decisions are made

- **Direction** is set per phase. Each phase has a written plan in [`docs/phases/`](docs/phases/),
  approved by the maintainer before implementation starts.
- **Changes** land only through pull requests into `main`. Each carries a written summary —
  impact, problem, approach, scenarios, evidence — that the maintainer approves before merge.
- **Design changes** produce a new versioned design document; earlier versions stay readable.
- **Progress** is tracked in each phase's GitHub milestone, its issues, and
  [`CHANGELOG.md`](CHANGELOG.md).

## Commitments

- The gateway, its adapters and single-tenant local use remain Apache-2.0.
- Correctness guardrails are never a paid feature.
- An engine is described as supported only while its conformance parity matrix is green.

## Contribution stages

| Stage | Issues | Pull requests | Entered when |
| --- | --- | --- | --- |
| 0 — closed (current) | collaborators only | collaborators only | — |
| 1 — issues open | everyone | collaborators only | Phase 1 closes; issue forms and a code of conduct land first |
| 2 — PRs open | everyone | everyone | the contributor licensing model is decided |

**Open decision before Stage 2:** a Contributor License Agreement or a Developer Certificate of
Origin. A CLA keeps the ability to relicense contributed code, which matters if a commercial tier
ever builds on it; a DCO is lighter for contributors and does not grant that ability.
