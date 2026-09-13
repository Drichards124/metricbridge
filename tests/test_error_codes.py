"""The error vocabulary is public API, so its documentation is checked like code."""

import re
from pathlib import Path

from metricbridge.contract.rules import CODES

DOC = Path(__file__).parents[1] / "docs" / "error-codes.md"


def documented() -> set[str]:
    return set(re.findall(r"^\| `([a-z_]+)` \|", DOC.read_text(), flags=re.MULTILINE))


def test_every_code_is_documented():
    assert CODES - documented() == set()


def test_no_code_is_documented_that_the_registry_cannot_raise():
    assert documented() - CODES == set()
