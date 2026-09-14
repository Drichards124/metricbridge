"""The command line. Refusals run as a real process: only the interpreter prints a traceback."""

import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from storefront_data import SCHEMA, storefront_database

from metricbridge import server as server_module

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"


def metricbridge(*arguments: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "metricbridge.server", *map(str, arguments)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def without_products(directory: Path) -> Path:
    path = directory / "seeded.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        connection.execute("DROP TABLE storefront.dim_products")
    return path


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        (lambda d: ("--manifest", d, "--duckdb", storefront_database()), "no manifest files found"),
        (
            lambda d: ("--manifest", STOREFRONT, "--duckdb", without_products(d)),
            "table 'storefront.dim_products' could not be read",
        ),
    ],
    ids=["invalid manifest", "warehouse contradicts the manifest"],
)
def test_a_refused_configuration_is_a_message_not_a_crash(tmp_path, setup, problem):
    """A traceback says the program broke. A refused manifest is the operator's to fix, so it gets
    the problem list on stderr and exit code 1, which supervisors read as a failed start. The
    engine's own log lines, naming each error class, may come first."""
    result = metricbridge(*setup(tmp_path))
    assert result.returncode == 1
    assert result.stdout == ""
    assert any(line.startswith("metricbridge: ") for line in result.stderr.splitlines())
    assert problem in result.stderr
    assert "Traceback" not in result.stderr


def test_only_a_manifest_refusal_is_caught(monkeypatch):
    """A bug in the validator is not the operator's to fix: it keeps its exception and traceback."""

    def broken(*_arguments, **_options):
        raise RuntimeError("a bug in the validator")

    monkeypatch.setattr(server_module, "build_server", broken)
    monkeypatch.setattr(
        "sys.argv",
        ["metricbridge", "--manifest", str(STOREFRONT), "--duckdb", str(storefront_database())],
    )
    with pytest.raises(RuntimeError):
        server_module.main()
