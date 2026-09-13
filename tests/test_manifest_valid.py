"""A valid manifest loads into the model the rest of the gateway relies on."""

import shutil
from pathlib import Path

from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"


def test_loads_every_file_in_a_directory():
    manifest = load_manifest(STOREFRONT)
    assert set(manifest.semantic_models) == {
        "orders",
        "customers",
        "inventory_snapshots",
        "subscription_revenue",
    }
    assert set(manifest.metrics) == {
        "revenue",
        "order_count",
        "average_order_value",
        "inventory_on_hand",
        "trailing_12m_revenue",
        "revenue_month_to_date",
    }


def test_loads_a_single_file():
    manifest = load_manifest(STOREFRONT / "inventory.yml")
    assert set(manifest.metrics) == {"inventory_on_hand"}


def test_joins_are_derived_from_entity_types():
    manifest = load_manifest(STOREFRONT)
    joins = {(j.from_model, j.to_model, j.entity, j.cardinality) for j in manifest.joins}
    assert joins == {
        ("orders", "customers", "customer", "many_to_one"),
        ("subscription_revenue", "customers", "customer", "many_to_one"),
    }


def test_partition_dimension_and_defaults():
    orders = load_manifest(STOREFRONT).semantic_models["orders"]
    assert orders.partition.name == "order_date"
    channel = next(d for d in orders.dimensions if d.name == "channel")
    assert channel.expr == "channel"
    assert load_manifest(STOREFRONT).semantic_models["customers"].partition is None


def test_snapshot_measure_declares_its_rollup():
    model = load_manifest(STOREFRONT).semantic_models["inventory_snapshots"]
    units = model.measures[0]
    assert units.non_additive_dimension is not None
    assert units.non_additive_dimension.name == "snapshot_date"
    assert units.non_additive_dimension.window_choice == "max"
    assert units.non_additive_dimension.window_groupings == ["product"]


def test_metric_types_carry_their_parameters():
    metrics = load_manifest(STOREFRONT).metrics
    assert metrics["revenue"].measure == "revenue"
    assert metrics["revenue"].tier == "certified"
    assert metrics["average_order_value"].numerator == "revenue"
    assert metrics["average_order_value"].denominator == "order_count"
    trailing = metrics["trailing_12m_revenue"]
    assert (trailing.window.count, trailing.window.granularity) == (12, "month")
    assert metrics["revenue_month_to_date"].grain_to_date == "month"
    assert metrics["order_count"].tier == "certified"


def test_version_is_deterministic_and_tracks_content_not_formatting(tmp_path):
    copy = tmp_path / "storefront"
    shutil.copytree(STOREFRONT, copy)
    original = load_manifest(copy).version
    assert original == load_manifest(STOREFRONT).version
    assert len(original) == 64

    orders = copy / "orders.yml"
    orders.write_text(
        "# formatting only\n\n"
        + orders.read_text().replace("  - name: customers", "\n  - name: customers")
    )
    assert load_manifest(copy).version == original

    orders.write_text(orders.read_text().replace("expr: amount}", "expr: amount_net}"))
    assert load_manifest(copy).version != original
