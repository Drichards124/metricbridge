"""The MCP surface, driven by a real client over in-memory streams — no subprocess, no network."""

from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from metricbridge.manifest import load_manifest
from metricbridge.server import build_server

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"


def exchange(conversation):
    """Run one client conversation against a server wired to the storefront manifest."""

    async def run():
        server = build_server(load_manifest(STOREFRONT))
        low = server._lowlevel_server
        async with (
            create_client_server_memory_streams() as ((cr, cw), (sr, sw)),
            anyio.create_task_group() as group,
        ):
            group.start_soon(
                lambda: low.run(sr, sw, low.create_initialization_options(), raise_exceptions=True)
            )
            async with ClientSession(cr, cw) as session:
                initialised = await session.initialize()
                result = await conversation(session, initialised)
            group.cancel_scope.cancel()
            return result

    return anyio.run(run)


@pytest.fixture(scope="module")
def tools():
    return exchange(lambda session, _: session.list_tools())


def test_the_server_introduces_itself(tools):
    async def conversation(session, initialised):
        return initialised

    initialised = exchange(conversation)
    assert initialised.server_info.name == "metricbridge"
    assert "never write SQL" in (initialised.instructions or "")


def test_only_the_governed_tools_are_exposed(tools):
    assert {tool.name for tool in tools.tools} == {"discover_metrics", "get_metric_signature"}


def test_the_descriptions_push_the_agent_down_the_protocol(tools):
    described = {tool.name: tool.description or "" for tool in tools.tools}
    assert "ALWAYS call this first" in described["discover_metrics"]
    assert "before query_metric" in described["get_metric_signature"]


def test_no_tool_accepts_sql(tools):
    for tool in tools.tools:
        assert "sql" not in str(tool.input_schema).lower()


def test_discovery_returns_candidates_with_a_preview():
    async def conversation(session, _):
        return await session.call_tool("discover_metrics", {"query": "units in stock"})

    payload = exchange(conversation).structured_content
    assert payload["ok"] is True
    assert len(payload["manifest_version"]) == 64
    first = payload["metrics"][0]
    assert first["metric"] == "inventory_on_hand"
    assert first["owner"] == "operations"
    assert "warehouse" in first["dimensions_preview"]


def test_discovery_hides_uncertified_metrics_by_default():
    async def conversation(session, _):
        certified = await session.call_tool("discover_metrics", {"query": "revenue"})
        everything = await session.call_tool(
            "discover_metrics", {"query": "revenue", "certified_only": False}
        )
        return certified, everything

    certified, everything = exchange(conversation)
    names = {m["metric"] for m in certified.structured_content["metrics"]}
    assert "gross_revenue" not in names
    assert "gross_revenue" in {m["metric"] for m in everything.structured_content["metrics"]}


def test_the_signature_is_the_whole_contract():
    async def conversation(session, _):
        return await session.call_tool("get_metric_signature", {"metric": "revenue"})

    payload = exchange(conversation).structured_content
    assert payload["ok"] is True
    signature = payload["signature"]
    assert signature["metric"] == "revenue"
    assert signature["time_grains"] == ["day", "week", "month", "quarter", "year"]
    assert signature["required_filters"][0]["field"] == "order_date"
    assert "customer__region" in {d["name"] for d in signature["dimensions"]}


def test_an_unknown_metric_comes_back_as_data_not_an_error():
    async def conversation(session, _):
        return await session.call_tool("get_metric_signature", {"metric": "revenu"})

    result = exchange(conversation)
    assert result.is_error is not True  # a refusal is an answer, not a protocol failure
    payload = result.structured_content
    assert payload["ok"] is False
    (refusal,) = payload["errors"]
    assert refusal["code"] == "unknown_metric"
    assert "revenue" in refusal["valid_alternatives"]
    assert refusal["remediation"]


def test_a_question_the_catalog_cannot_answer_asks_instead_of_guessing():
    """ "How much did we sell" shares no word with any metric. Guessing here picks revenue or order
    count by luck; the reply instead carries the catalog and tells the agent to ask the user."""

    async def conversation(session, _):
        return await session.call_tool("discover_metrics", {"query": "how much did we sell"})

    payload = exchange(conversation).structured_content
    assert payload["ok"] is False
    (refusal,) = payload["errors"]
    assert refusal["code"] == "no_match"
    assert "Ask the user" in refusal["remediation"]
    assert "revenue" in {entry["metric"] for entry in payload["catalog"]}


def test_a_weak_match_is_flagged_rather_than_presented_as_an_answer():
    async def conversation(session, _):
        return await session.call_tool("discover_metrics", {"query": "customer"})

    payload = exchange(conversation).structured_content
    assert payload["ok"] is True
    assert payload["confident"] is False
    assert "Ask the user" in payload["clarify"]["reason"]
    assert all(metric["confident"] is False for metric in payload["metrics"])


def test_a_strong_match_is_returned_without_a_clarification_prompt():
    async def conversation(session, _):
        return await session.call_tool("discover_metrics", {"query": "revenue by region"})

    payload = exchange(conversation).structured_content
    assert payload["confident"] is True
    assert payload["clarify"] is None
    assert payload["metrics"][0]["metric"] == "revenue"


def test_the_description_tells_the_agent_to_ask_rather_than_retry(tools):
    described = {tool.name: tool.description or "" for tool in tools.tools}
    assert "ASK THE USER" in described["discover_metrics"]
