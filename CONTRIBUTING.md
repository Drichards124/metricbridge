# Contributing to MetricBridge

Thank you for your interest. **MetricBridge is not accepting outside issues or pull requests
yet.** The repository is public so the work can be followed as it happens.

## Why contributions are closed for now

- The contracts are still moving: MCP tool schemas, the error-code vocabulary and the manifest
  format all change during Phase 1. Contributions against them would be rework for everyone.
- The correctness baseline — conformance suite, reference evaluator, parity matrix — does not
  exist yet. Until it does, there is no objective way to review a change for correctness.
- The contributor licensing model has not been decided (see [GOVERNANCE.md](GOVERNANCE.md)).

## What opens them

Contributions open in stages once Phase 1 closes, announced in the [changelog](CHANGELOG.md) and
the README:

1. **Issues** — bug reports and metric-semantics edge cases.
2. **Pull requests** — after the contributor licensing model is in place.

## Until then

- Watch the repository for releases.
- Report security vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## When pull requests open

These rules will apply, and already bind the maintainer:

- Every PR uses the [pull request template](.github/pull_request_template.md): impact, problem,
  approach, scenarios, red → green evidence.
- Behaviour changes start with a failing test.
- A conformance divergence is fixed in the compiler or a documented normalisation rule — never
  by editing the expected value.
- Every constraint the validator enforces is discoverable in `get_metric_signature`.
