"""Warehouse verification: a manifest the warehouse contradicts is refused before serving."""

import tempfile
from pathlib import Path

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from storefront_data import SCHEMA, storefront_database

from metricbridge.engine import DuckDBEngine
from metricbridge.manifest import ManifestError, load_manifest
from metricbridge.server import build_server
from metricbridge.verify import verify

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"
CUSTOMER = "INSERT INTO storefront.dim_customers VALUES ({}, 'EMEA', 'smb', '2026-01-01')"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


def database(directory: Path, *changes: str, name: str = "seeded.duckdb") -> DuckDBEngine:
    """The storefront schema with some changes, opened the way the server opens it."""
    built = directory / "building.duckdb"
    with duckdb.connect(str(built)) as connection:
        connection.execute(SCHEMA)
        for change in changes:
            connection.execute(change)
    # Renamed after it is built: DuckDB cannot even create tables in a file named like a schema.
    return DuckDBEngine(built.rename(directory / name))


def problems(manifest, engine) -> dict[str, str]:
    with pytest.raises(ManifestError) as refused:
        verify(manifest, engine)
    return {issue.path: issue.message for issue in refused.value.issues}


def test_the_seeded_database_matches_the_manifest(manifest):
    verify(manifest, DuckDBEngine(storefront_database()))


def test_a_missing_table_is_refused_once_not_per_column(manifest, tmp_path):
    engine = database(tmp_path, "DROP TABLE logistics.fct_shipments")
    found = problems(manifest, engine)
    assert list(found) == ["shipments"]
    assert "logistics.fct_shipments" in found["shipments"]


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ("ALTER TABLE storefront.dim_customers DROP COLUMN region", "customers.dimensions.region"),
        (
            "ALTER TABLE storefront.fct_order_lines DROP COLUMN order_id",
            "orders.measures.order_count",
        ),
        (
            "ALTER TABLE storefront.fct_order_lines DROP COLUMN customer_id",
            "orders.entities.customer",
        ),
    ],
)
def test_an_expression_the_table_cannot_evaluate_is_refused(manifest, tmp_path, change, path):
    assert list(problems(manifest, database(tmp_path, change))) == [path]


def test_a_duplicated_join_key_is_refused_without_quoting_its_values(manifest, tmp_path, caplog):
    """An "N:1" join to customers fans out when a customer appears twice: every measure inflates,
    and nothing errors at query time. Key values are data, so only their count is reported."""
    engine = database(tmp_path, *(CUSTOMER.format(key) for key in (7001, 7001, 7002, 7002, 7003)))
    found = problems(manifest, engine)
    assert list(found) == ["customers.entities.customer"]
    assert "2 value" in found["customers.entities.customer"]
    assert "7001" not in str(found) and "7001" not in caplog.text


def test_empty_keys_are_not_duplicates(manifest, tmp_path):
    """A NULL key matches nothing in a join, so repeating it cannot fan anything out."""
    verify(manifest, database(tmp_path, CUSTOMER.format("NULL"), CUSTOMER.format("NULL")))


def test_a_key_no_join_targets_is_not_scanned(manifest, tmp_path):
    """Nothing joins to an order line, so a repeated one cannot inflate a measure."""
    line = (
        "INSERT INTO storefront.fct_order_lines VALUES (1, 1, 1, 1, '2026-07-03', NULL, 'web', 1)"
    )
    verify(manifest, database(tmp_path, line, line))


def test_every_problem_is_reported_at_once(manifest, tmp_path):
    engine = database(
        tmp_path,
        "DROP TABLE storefront.dim_products",
        CUSTOMER.format(1),
        CUSTOMER.format(1),
        "ALTER TABLE storefront.dim_customers DROP COLUMN segment",
    )
    assert set(problems(manifest, engine)) == {
        "products",
        "customers.dimensions.segment",
        "customers.entities.customer",
    }


def test_a_database_file_named_like_a_schema_says_to_rename_it(manifest, tmp_path):
    """DuckDB names the database after its file, which makes `storefront.<table>` ambiguous."""
    found = problems(manifest, database(tmp_path, name="storefront.duckdb"))
    assert len(found) == 1
    (message,) = found.values()
    assert "rename" in message and "'storefront'" in message


def test_the_server_will_not_serve_a_manifest_the_warehouse_contradicts(manifest, tmp_path):
    engine = database(tmp_path, "DROP TABLE storefront.dim_customers")
    with pytest.raises(ManifestError):
        build_server(manifest, engine)


@settings(max_examples=30, deadline=None)
@given(st.lists(st.one_of(st.none(), st.integers(min_value=1, max_value=4)), max_size=8))
def test_a_key_is_refused_exactly_when_a_value_repeats(manifest, keys):
    present = [key for key in keys if key is not None]
    repeated = {key for key in present if present.count(key) > 1}
    with tempfile.TemporaryDirectory() as directory:
        engine = database(Path(directory), *(CUSTOMER.format(key or "NULL") for key in keys))
        if not repeated:
            verify(manifest, engine)
            return
        found = problems(manifest, engine)
    assert list(found) == ["customers.entities.customer"]
    assert f"{len(repeated)} value" in found["customers.entities.customer"]


# Columns read by exactly one element, so dropping one names exactly that element.
SOLE_READERS = {
    ("storefront.dim_customers", "region"): "customers.dimensions.region",
    ("storefront.dim_customers", "segment"): "customers.dimensions.segment",
    ("storefront.fct_order_lines", "channel"): "orders.dimensions.channel",
    ("storefront.fct_order_lines", "order_id"): "orders.measures.order_count",
    ("logistics.fct_shipments", "carrier"): "shipments.dimensions.carrier",
    ("warehouse.fct_inventory_daily", "warehouse"): "inventory_snapshots.dimensions.warehouse",
}


@settings(max_examples=30, deadline=None)
@given(st.sets(st.sampled_from(sorted(SOLE_READERS))))
def test_exactly_the_elements_whose_columns_are_missing_are_reported(manifest, dropped):
    changes = [f"ALTER TABLE {table} DROP COLUMN {column}" for table, column in dropped]
    with tempfile.TemporaryDirectory() as directory:
        engine = database(Path(directory), *changes)
        if not dropped:
            verify(manifest, engine)
            return
        found = problems(manifest, engine)
    assert set(found) == {SOLE_READERS[column] for column in dropped}
