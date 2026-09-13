"""The MCP server: three tools, and never a SQL string from the agent.

Tool descriptions are product surface. An agent reads them before deciding anything, so "call this
first" in a description does more for correct behaviour than any amount of server-side validation:
it shapes the first move instead of punishing the wrong one.

Errors come back as data (`ok: false` with structured refusals), never as protocol errors. An agent
can act on a payload that names the offending field and the authorised alternatives; it cannot act
on an exception.
"""

import argparse
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .contract import signature as metric_signature
from .contract import unknown_metric
from .discovery import MetricIndex
from .manifest import SemanticManifest, load_manifest

INSTRUCTIONS = """MetricBridge serves governed metrics from a certified semantic manifest.

Ask for a metric by name with structured arguments; you never write SQL, and no tool accepts it.
The path is always: discover_metrics -> get_metric_signature -> query_metric.

A refused request comes back with `ok: false` and a stable error code, the offending field, a
remediation and the authorised alternatives. Repair from those rather than rephrasing."""

DISCOVER_DESCRIPTION = """Find governed metrics by intent. ALWAYS call this first, before any \
other metric tool, and before assuming a metric or a cut exists.

Give a natural-language description of what you want to measure. Returns candidate metrics with \
their certified definition, owner and tier, plus a preview of the dimension names each one \
authorises. The preview is names only: call get_metric_signature for the full contract before \
querying, because a cut that looks obvious may not be authorised for that metric."""

SIGNATURE_DESCRIPTION = """Get the exhaustive contract for one metric. ALWAYS call this after \
discover_metrics and before query_metric.

Returns the authorised cuts (with the qualified names to use), the supported time grains, the \
mandatory date range and its maximum window, the filter operators, any filters the definition \
always applies, ordering rules and row limits. Everything the gateway enforces is listed here, so \
a request built from this signature is not guessed."""

PREVIEW_LIMIT = 12


def build_server(manifest: SemanticManifest) -> MCPServer:
    index = MetricIndex(manifest)
    server = MCPServer(name="metricbridge", instructions=INSTRUCTIONS)

    @server.tool(name="discover_metrics", description=DISCOVER_DESCRIPTION, structured_output=True)
    def discover_metrics(
        query: str,
        domain: str | None = None,
        certified_only: bool = True,
        limit: int = 5,
    ) -> dict[str, Any]:
        found = index.search(query, domain=domain, certified_only=certified_only, limit=limit)
        return {
            "ok": True,
            "manifest_version": manifest.version,
            "metrics": [
                {
                    "metric": metric.name,
                    "description": metric.description,
                    "type": metric.type,
                    "tier": metric.tier,
                    "owner": metric.owner,
                    "domain": metric.domain,
                    "score": score,
                    "dimensions_preview": [
                        entry["name"]
                        for entry in metric_signature(manifest, metric.name)["dimensions"]
                    ][:PREVIEW_LIMIT],
                }
                for metric, score in found
            ],
            "domains": index.domains(),
        }

    @server.tool(
        name="get_metric_signature", description=SIGNATURE_DESCRIPTION, structured_output=True
    )
    def get_metric_signature(metric: str) -> dict[str, Any]:
        if metric not in manifest.metrics:
            return {"ok": False, "errors": [unknown_metric(manifest, metric).as_dict()]}
        return {
            "ok": True,
            "manifest_version": manifest.version,
            "signature": metric_signature(manifest, metric),
        }

    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="metricbridge", description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=os.environ.get("METRICBRIDGE_MANIFEST"),
        help="directory or file of semantic manifest YAML (or set METRICBRIDGE_MANIFEST)",
    )
    parser.add_argument("--transport", default="stdio", choices=("stdio", "sse", "streamable-http"))
    arguments = parser.parse_args()
    if arguments.manifest is None:
        parser.error("no manifest: pass --manifest or set METRICBRIDGE_MANIFEST")
    build_server(load_manifest(arguments.manifest)).run(transport=arguments.transport)


if __name__ == "__main__":
    main()
