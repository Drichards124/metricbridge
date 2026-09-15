"""The conformance corpus: every case, on every available engine, against its reference.

Only DuckDB is available until the Postgres and ClickHouse adapters exist.
"""

from collections import Counter
from pathlib import Path

import pytest
from harness import compare, load_case, run_case

CASES = Path(__file__).parent / "cases"
PATHS = sorted(p for p in CASES.rglob("*") if p.suffix in (".yml", ".yaml"))


def test_the_corpus_is_collected():
    # pytest skips a parametrised test with no cases, which would read as a pass.
    assert PATHS, f"no case files under {CASES}"
    repeated = [stem for stem, count in Counter(p.stem for p in PATHS).items() if count > 1]
    assert not repeated, f"case ids used more than once: {repeated}"


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
def test_case(path):
    case = load_case(path)

    answer, reference = run_case(case)

    differences = compare(answer, case, reference)
    if case.known_divergence:
        # Strict: once MetricBridge agrees, the mark has to go, or the catalog records a closed gap.
        assert differences, f"agrees now: remove known_divergence {case.known_divergence!r}"
        pytest.xfail(f"known divergence: {case.known_divergence}")
    assert differences == []
