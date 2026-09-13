# Security threat model

## Purpose

MetricBridge sits between an autonomous agent and a data warehouse, holding warehouse credentials
the agent never sees. This document says who is trusted, where the boundaries are, and which
failures we treat as vulnerabilities. Reporting process: [SECURITY.md](SECURITY.md).

## Scope

The gateway, its manifest loader, contract, compiler and guardrails. Not the warehouse's own access
control, not the agent's model, and not the MCP client.

## Security goals

1. **Agent input never becomes SQL text.** Agents send structured requests; values bind as
   parameters. There is no tool that accepts SQL.
2. **Nothing outside the manifest is reachable.** A table absent from a semantic model cannot be
   read, however a request is phrased.
3. **No scan is unbounded.** Every statement is bounded on a time column and carries a row limit.
4. **Result values stay out of logs and traces.** Telemetry records what was asked and what it cost.

## Roles

| Role | Trusted with | Not trusted with |
| --- | --- | --- |
| **Agent** | Choosing a metric and its arguments | Anything that becomes SQL syntax |
| **Manifest author** (analytics engineer) | Declaring tables, columns, expressions, filters | — their declarations are SQL fragments and are trusted by design |
| **Operator** | Warehouse credentials, connection configuration, row and spend limits | — |
| **Warehouse** | Storage and execution, its own permissions | Being the only guard: the gateway bounds every scan itself |

## Trust boundaries

**Boundary 1 — agent to gateway.** Everything an agent sends is untrusted. Metric names, dimension
names, grains and operators are matched against the manifest; filter values are bound, never
rendered. A prompt-injected document can at worst produce a request for a metric that does not
exist, which is refused with a structured error.

**Boundary 2 — manifest to gateway.** A manifest is trusted input, written by someone who already
has warehouse access. Its `expr` fields are SQL fragments and are compiled as given. A manifest
author can therefore express anything their own database permissions allow; this is a deliberate
design position, not an oversight. Manifests should be reviewed like code and loaded from a
trusted path.

**Boundary 3 — gateway to warehouse.** The generated statement is re-parsed and asserted before
execution: one SELECT, declared tables only, every scan bounded, no `SELECT *`, a row limit within
the ceiling. These assertions run on the syntax tree, because string matching fails open on the
first comment or subquery.

## In scope — treated as vulnerabilities

1. **Agent input reaching SQL text.** Any path by which a request value is rendered into the
   statement rather than bound.
2. **Guardrail bypass.** A statement reaching the warehouse that is not a single bounded SELECT over
   declared tables — including an unbounded CTE leg inside an otherwise compliant statement.
3. **Reaching an undeclared table, metric or tenant.**
4. **Credential or result disclosure**: values in logs, traces or error messages.

## Where we differ from comparable projects

Several projects class **silently wrong results** as ordinary correctness bugs. Here, a wrong number
that raises no error is the failure the product exists to prevent, so a defect in the gateway that
produces one — a dropped date bound, a fanned-out join, a snapshot summed across time — is treated
with the same urgency as a security issue, even though it is not a privilege violation. The
catalogue of those failures and their guards is [docs/failure-modes.md](docs/failure-modes.md).

## Usually out of scope

1. **A manifest that declares the wrong table or expression.** The gateway compiles what the author
   declared; that is Boundary 2.
2. **Warehouse permissions.** If the configured credential can read a table, so can a metric over it.
   Grant the gateway only what its metrics need.
3. **Denial of service through expensive queries.** Bounded scans, row caps and (from 1.6) statement
   timeouts limit damage; a determined operator-authorised agent can still spend money.
4. **The MCP client's behaviour**, including whether an elicitation reaches a human.
