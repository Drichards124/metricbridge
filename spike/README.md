# Spike — not the implementation

Throwaway code written before the architecture was settled. It exists for one reason: it
**validated the stack against reality**, and one finding changed the plan.

- `mcp` 2.2.0 **renamed FastMCP to `MCPServer`** (`from mcp.server.mcpserver import MCPServer`).
  Every FastMCP snippet in circulation — including the one in the original MetricBridge
  spec — targets v1 and fails on import.
- `MCPServer.run(transport=...)` already accepts `stdio`, `sse` and `streamable-http`, so
  remote transport is a configuration choice, not a phase-3 project.
- Tool input schemas are derived from the Python function signature in v2; hand-written
  JSON Schema is not the integration path.
- Verified versions: `sqlglot` 30.18.0, `duckdb` 1.5.5.

Nothing here is reviewed, tested, or authoritative. Read `docs/metricbridge-architecture-v0.html`
first; this gets rewritten or deleted once the plan is approved.
