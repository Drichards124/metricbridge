# MetricBridge

A deterministic semantic firewall between autonomous agents and data warehouses.

> **Status: pre-alpha.** Design is complete and Phase 1 is under way. There is no release yet —
> do not point this at a production warehouse.

Agents connected to a warehouse through a raw `execute_sql` tool write confident, wrong SQL:
invented joins, refunds counted as revenue, snapshot measures summed across time. Nothing errors,
so nobody notices.

MetricBridge is an MCP server that inverts who writes the query:

- **Agents ask for governed metrics** in structured form — metric, dimensions, grain, date range.
  They never supply SQL.
- **MetricBridge compiles the SQL** from a certified definition, verifies it on the syntax tree
  (partition bounds, declared tables only, row limits) and binds every value as a parameter.
- **Bad requests are refused with the fix attached** — a stable error code, the offending field
  and the valid alternatives — so the agent corrects itself instead of returning a plausible
  wrong number.

```text
query_metric(stock_level, time_grain="month")   # a snapshot measure with no declared rollup
→ refused: non_additive_cut — stock_level is a snapshot; summing a month of days counts stock ~30 times.
  remediation: declare how it rolls up (for example, the last snapshot in each period), or query by day.
```

## Correctness is the product

Every supported engine must return the same answer as an independent reference implementation.
Phase 1 proves this with hand-written reference SQL, cross-engine agreement on DuckDB, Postgres,
ClickHouse, BigQuery and Snowflake, and a nightly differential run of 3,000,000 randomised
requests per local engine. An engine is listed as supported only while its parity matrix is green.

## Project status

- [Phase 1 milestone](https://github.com/Drichards124/metricbridge/milestone/1): progress, one issue per deliverable
- [Phase 1 plan](docs/phases/phase-1.md)
- [Design record, v0](docs/metricbridge-architecture-v0.html)
- [Releasing](RELEASING.md) · [Governance](GOVERNANCE.md) · [Changelog](CHANGELOG.md)

## Contributing

Outside contributions are not open yet — see [CONTRIBUTING.md](CONTRIBUTING.md) for why and
what opens them. Security reports: [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE).
