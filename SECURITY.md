# Security policy

## Supported versions

No version has been released. From the first release, and while MetricBridge is `0.x`, only the
latest minor release receives security fixes.

## Reporting a vulnerability

Report privately through GitHub: **Security → Report a vulnerability** on this repository.
Please do not open a public issue.

Include what you did, what happened, and the version or commit. You will receive an
acknowledgement within 7 days. Confirmed vulnerabilities are fixed in an out-of-schedule patch
release (see [RELEASING.md](RELEASING.md)) and credited in the changelog unless you prefer otherwise.

## In scope

MetricBridge holds warehouse credentials and answers on behalf of agents, so these are treated as
vulnerabilities rather than bugs:

- Any path by which agent-supplied input reaches SQL text instead of a bound parameter.
- Any query that reaches the warehouse without passing the AST guardrails — an unbounded scan, an
  undeclared table, a stacked statement.
- Access to a metric, table or tenant the manifest does not authorise.
- Result values written to logs, traces or error messages.
