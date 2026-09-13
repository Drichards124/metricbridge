"""`spike/` validated the stack and is not the implementation: nothing under `src/` may import it."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "metricbridge"


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_implementation_never_imports_spike():
    files = sorted(SRC.rglob("*.py"))
    assert files, f"no implementation found under {SRC}"
    offenders = [str(p.relative_to(SRC)) for p in files if "spike" in _imported_roots(p)]
    assert offenders == []
