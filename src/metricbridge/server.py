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
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field, create_model

from .compiler import compile_query
from .contract import (
    DEFAULT_ROW_LIMIT,
    DateRangeInput,
    FilterInput,
    OrderByInput,
    QueryRequest,
    RefusalError,
    unknown_metric,
    validate,
)
from .contract import signature as metric_signature
from .discovery import MetricIndex
from .engine import DuckDBEngine, Engine, execute
from .manifest import SemanticManifest, load_manifest
from .verify import verify

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
querying, because a cut that looks obvious may not be authorised for that metric.

If the result is not confident, or nothing matched, ASK THE USER which metric they mean, using \
the catalog returned in the reply. Do not rephrase and retry blindly, and do not pick a metric \
that merely shares a word with the question: the wrong metric answers with a plausible number."""

SIGNATURE_DESCRIPTION = """Get the exhaustive contract for one metric. ALWAYS call this after \
discover_metrics and before query_metric.

Returns the authorised cuts (with the qualified names to use), the supported time grains, the \
mandatory date range and its maximum window, the filter operators, any filters the definition \
always applies, ordering rules and row limits. Everything the gateway enforces is listed here, so \
a request built from this signature is not guessed."""

QUERY_DESCRIPTION = """Answer a governed metric. ALWAYS call this after get_metric_signature, and \
build the request from that signature: every constraint it lists is enforced here.

Send structured arguments only: the metric, a mandatory date_range, and any authorised \
dimensions, time_grain, filters, order_by and row_limit. The gateway builds, checks and runs the \
query itself.

The reply states every absence instead of leaving a gap. `missing_periods` lists periods the data \
never produced; they are absent, not zero. `null_key_rows` counts, per joined entity, rows whose \
join key was empty. It does not count keys that match no row in the joined table, so `{}` does not \
mean every row reconciled. `null` means nothing was counted: either this metric shape does not \
count, or the row limit cut the answer off. `row_limit_reached` means rows were cut off, so \
nothing is claimed about the rest. Exact decimals come back as strings."""

# Raised by discovery rather than by a contract rule, so it lives here, not in the rule registry.
DISCOVERY_CODES = frozenset({"no_match"})

PREVIEW_LIMIT = 12
CATALOG_LIMIT = 20
# Below this, a hit shares a word with the catalog but does not answer the question. Asking beats
# guessing: a similarity score cannot tell revenue from order count when someone says "sell".
CONFIDENT_SCORE = 1.0


async def ask_which_metric(ctx: Context, query: str, options: list[str]) -> str | None:
    """Put the question to the person, where the client can carry one.

    A similarity score cannot tell revenue from order count when someone says "sell". A human can,
    in one turn — and their answer is also the evidence that a synonym is missing from the manifest.
    Clients without the capability fall through to the catalog reply, so nothing breaks.
    """
    if not options:
        return None
    try:
        capabilities = ctx.client_capabilities
    except (LookupError, AttributeError):  # called outside a request, as in unit tests
        return None
    if capabilities is None or getattr(capabilities, "elicitation", None) is None:
        return None

    schema = create_model(
        "MetricChoice",
        metric=(
            Literal[tuple(options)],
            Field(description="the governed metric that answers the question"),
        ),
    )
    answer = await ctx.elicit(
        message=(
            f"No certified metric clearly matches {query!r}. Which of these did you mean? "
            "Cancel if none of them do."
        ),
        schema=schema,
    )
    if answer.action == "accept" and answer.data is not None:
        return answer.data.metric
    return None


def build_server(
    manifest: SemanticManifest, engine: Engine, misses: Counter | None = None
) -> MCPServer:
    """`misses` counts phrasings the catalog could not answer confidently.

    Each one is either a synonym missing from the manifest — a deterministic fix that helps every
    later question — or, in volume, the measured recall failure that would justify reaching for
    embeddings. Guessing produces neither.

    Raises `ManifestError` when the warehouse contradicts the manifest, before any tool exists.
    """
    verify(manifest, engine)
    index = MetricIndex(manifest)
    misses = misses if misses is not None else Counter()
    server = MCPServer(name="metricbridge", instructions=INSTRUCTIONS)

    def catalog(certified_only: bool) -> list[dict[str, Any]]:
        """What is actually on offer — the answer to "then what can I ask for?"."""
        return [
            {
                "metric": metric.name,
                "description": metric.description,
                "domain": metric.domain,
                "tier": metric.tier,
            }
            for metric in sorted(manifest.metrics.values(), key=lambda m: m.name)
            if not certified_only or metric.tier == "certified"
        ][:CATALOG_LIMIT]

    @server.tool(name="discover_metrics", description=DISCOVER_DESCRIPTION, structured_output=True)
    async def discover_metrics(
        query: str,
        ctx: Context,
        domain: str | None = None,
        certified_only: bool = True,
        limit: int = 5,
    ) -> dict[str, Any]:
        found = index.search(query, domain=domain, certified_only=certified_only, limit=limit)
        confident = bool(found) and found[0][1] >= CONFIDENT_SCORE
        chosen = None
        if not confident:
            misses[query] += 1
            chosen = await ask_which_metric(
                ctx, query, [entry["metric"] for entry in catalog(certified_only)]
            )
            if chosen is not None:
                found = [(manifest.metrics[chosen], CONFIDENT_SCORE)]
                confident = True
        if not found:
            return {
                "ok": False,
                "manifest_version": manifest.version,
                "errors": [
                    {
                        "code": "no_match",
                        "message": f"no governed metric matches {query!r}.",
                        "field": "query",
                        "offending_value": query,
                        "remediation": (
                            "Ask the user which of the catalogued metrics they mean, or which "
                            "words their team uses for it. Do not guess from a partial word match."
                        ),
                        "valid_alternatives": [e["metric"] for e in catalog(certified_only)],
                    }
                ],
                "catalog": catalog(certified_only),
                "domains": index.domains(),
            }
        return {
            "ok": True,
            "confident": confident,
            "chosen_by_user": chosen,
            "clarify": None
            if confident
            else {
                "reason": (
                    f"nothing matched {query!r} strongly; these merely share a word with it. "
                    "Ask the user which they mean before querying."
                ),
                "catalog": catalog(certified_only),
            },
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
                    "confident": score >= CONFIDENT_SCORE,
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

    @server.tool(name="query_metric", description=QUERY_DESCRIPTION, structured_output=True)
    def query_metric(
        metric: str,
        date_range: DateRangeInput | None = None,
        dimensions: list[str] | None = None,
        time_grain: str | None = None,
        filters: list[FilterInput] | None = None,
        order_by: list[OrderByInput] | None = None,
        row_limit: int = DEFAULT_ROW_LIMIT,
    ) -> dict[str, Any]:
        request = QueryRequest(
            metric=metric,
            date_range=date_range,
            dimensions=dimensions or [],
            time_grain=time_grain,
            filters=filters or [],
            order_by=order_by or [],
            row_limit=row_limit,
        )
        try:
            resolved = validate(manifest, request)
            query = compile_query(manifest, resolved, dialect=engine.dialect)
            answer = execute(manifest, resolved, query, engine)
        except RefusalError as refused:
            return refused.payload()
        return {
            "ok": True,
            "manifest_version": manifest.version,
            "metric": metric,
            **answer,
            "notices": resolved.notices,
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
    parser.add_argument(
        "--duckdb", type=Path, required=True, help="DuckDB database file to query, opened read-only"
    )
    parser.add_argument(
        "--statement-timeout",
        type=float,
        default=30.0,
        help="seconds a statement may run before it is stopped (default: 30)",
    )
    parser.add_argument("--transport", default="stdio", choices=("stdio", "sse", "streamable-http"))
    arguments = parser.parse_args()
    if arguments.manifest is None:
        parser.error("no manifest: pass --manifest or set METRICBRIDGE_MANIFEST")
    engine = DuckDBEngine(arguments.duckdb, statement_timeout=arguments.statement_timeout)
    build_server(load_manifest(arguments.manifest), engine).run(transport=arguments.transport)


if __name__ == "__main__":
    main()
