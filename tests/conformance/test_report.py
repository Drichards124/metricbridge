"""The generated conformance documents: what they say must follow from the run, and nothing else."""

import pytest
import tpch_data
from report import DOCUMENTS, DOCUMENTS_SCALE, EngineRun, Result, collect, render

DUCKDB = EngineRun(name="DuckDB", version="1.5.5")


def results(*rows):
    return [
        Result(catalog=catalog, case=case, engine="DuckDB", outcome=outcome, **extra)
        for catalog, case, outcome, extra in rows
    ]


class TestParity:
    def test_each_engine_is_named_with_its_version_and_each_case_with_its_outcome(self):
        documents = render(
            results(
                ("tpch", "net_revenue_by_year", "pass", {}),
                ("storefront", "revenue_total_q3", "pass", {}),
                (
                    "storefront",
                    "revenue_by_channel",
                    "known_divergence",
                    {"divergence": "Anchor dropping", "differences": ["row 2: x"]},
                ),
            ),
            engines=[DUCKDB],
            tpch_scale=0.1,
        )

        parity = documents["parity.md"]
        assert "| DuckDB | 1.5.5 | 3 | 2 | 1 | 0 | supported |" in parity
        assert "TPC-H scale factor: 0.1" in parity
        rows = [line for line in parity.splitlines() if line.startswith(("| storefront", "| tpch"))]
        assert rows == [
            "| storefront | revenue_by_channel | known divergence |",
            "| storefront | revenue_total_q3 | pass |",
            "| tpch | net_revenue_by_year | pass |",
        ]

    def test_an_unrecorded_failure_makes_the_engine_experimental(self):
        # Supported only while every case passes or is a divergence recorded in failure-modes.
        documents = render(
            results(
                ("tpch", "net_revenue_by_year", "fail", {"differences": ["row 0: x"]}),
                (
                    "storefront",
                    "revenue_by_channel",
                    "known_divergence",
                    {"divergence": "Anchor dropping", "differences": ["row 2: x"]},
                ),
            ),
            engines=[DUCKDB],
            tpch_scale=0.1,
        )

        assert "| DuckDB | 1.5.5 | 2 | 0 | 1 | 1 | experimental |" in documents["parity.md"]


class TestDivergences:
    def test_each_recorded_divergence_lists_its_cases_and_what_differed(self):
        documents = render(
            results(
                (
                    "storefront",
                    "inventory_by_month",
                    "known_divergence",
                    {"divergence": "Snapshot keeps one row", "differences": ["row 0, x: 1 vs 2"]},
                ),
                (
                    "storefront",
                    "trailing_by_month",
                    "known_divergence",
                    {"divergence": "Anchor dropping", "differences": ["row count", "row 2, y"]},
                ),
                ("storefront", "revenue_total_q3", "pass", {}),
            ),
            engines=[DUCKDB],
            tpch_scale=0.1,
        )

        sections = documents["divergences.md"].split("\n## ")[1:]
        assert sections == [
            (
                "Anchor dropping\n\n"
                "- DuckDB · storefront/trailing_by_month\n  - row count\n  - row 2, y\n"
            ),
            (
                "Snapshot keeps one row\n\n"
                "- DuckDB · storefront/inventory_by_month\n  - row 0, x: 1 vs 2\n"
            ),
        ]


@pytest.mark.skipif(
    tpch_data.SCALE != DOCUMENTS_SCALE,
    reason="the committed documents are generated at TPC-H scale factor 0.1",
)
def test_the_committed_documents_match_a_fresh_run():
    for name, text in render(*collect()).items():
        committed = DOCUMENTS / name
        assert committed.exists() and committed.read_text() == text, (
            f"docs/conformance/{name} is stale: run `make conformance-docs` and commit the result"
        )
