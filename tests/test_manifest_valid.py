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
        "products",
        "inventory_snapshots",
        "subscription_revenue",
    }
    assert set(manifest.metrics) == {
        "revenue",
        "order_count",
        "average_order_value",
        "gross_revenue",
        "web_revenue",
        "enterprise_revenue",
        "inventory_on_hand",
        "stock_level",
        "trailing_12m_revenue",
        "revenue_month_to_date",
    }


def test_loads_a_single_file():
    manifest = load_manifest(STOREFRONT / "products.yml")
    assert set(manifest.semantic_models) == {"products"}


def test_joins_are_derived_from_entity_types():
    manifest = load_manifest(STOREFRONT)
    joins = {(j.from_model, j.to_model, j.entity, j.cardinality) for j in manifest.joins}
    assert joins == {
        ("orders", "customers", "customer", "many_to_one"),
        ("orders", "products", "product", "many_to_one"),
        ("inventory_snapshots", "products", "product", "many_to_one"),
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
    units = next(m for m in model.measures if m.name == "units_on_hand")
    assert units.additive is False
    assert units.non_additive_dimension is not None
    assert units.non_additive_dimension.name == "snapshot_date"
    assert units.non_additive_dimension.window_choice == "max"
    assert units.non_additive_dimension.window_groupings == ["product"]

    undeclared = next(m for m in model.measures if m.name == "units_on_hand_raw")
    assert undeclared.additive is False
    assert undeclared.non_additive_dimension is None


def test_metric_types_carry_their_parameters():
    metrics = load_manifest(STOREFRONT).metrics
    assert metrics["revenue"].measure == "revenue"
    assert metrics["revenue"].tier == "certified"
    assert metrics["average_order_value"].numerator == "revenue"
    assert metrics["average_order_value"].denominator == "order_count"
    trailing = metrics["trailing_12m_revenue"]
    assert (trailing.window.count, trailing.window.granularity) == (12, "month")
    assert metrics["revenue_month_to_date"].grain_to_date == "month"
    assert metrics["gross_revenue"].tier == "deprecated"
    assert metrics["gross_revenue"].replaced_by == "revenue"


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


def test_declared_values_are_matched_case_insensitively(tmp_path):
    """Real manifests write `agg: SUM` and `type: SIMPLE`; refusing those is an interop bug."""
    (tmp_path / "shouty.yml").write_text(
        "semantic_models:\n"
        "  - name: orders\n"
        "    table: shop.orders\n"
        "    entities:\n"
        "      - {name: order, type: PRIMARY, expr: order_id}\n"
        "    dimensions:\n"
        "      - {name: order_date, type: TIME, time_granularity: DAY, is_partition: true}\n"
        "    measures:\n"
        "      - {name: revenue, agg: SUM, expr: amount}\n"
        "metrics:\n"
        "  - {name: revenue, type: SIMPLE, measure: revenue, description: Revenue.}\n"
    )
    manifest = load_manifest(tmp_path)
    model = manifest.semantic_models["orders"]
    assert model.entities[0].type == "primary"
    assert model.dimensions[0].type == "time"
    assert model.dimensions[0].time_granularity == "day"
    assert model.measures[0].agg == "sum"
    assert manifest.metrics["revenue"].type == "simple"


def test_metric_level_filters_are_part_of_the_definition():
    """A certified definition often is a filter: "revenue" means amount excluding refunds."""
    metrics = load_manifest(STOREFRONT).metrics
    (web,) = metrics["web_revenue"].filters
    assert (web.field, web.operator, web.value) == ("channel", "=", "web")

    (enterprise,) = metrics["enterprise_revenue"].filters
    assert enterprise.field == "customer__segment"
    assert enterprise.operator == "in"  # declared as IN; matched case-insensitively
    assert enterprise.value == ["enterprise", "strategic"]

    assert metrics["revenue"].filters == []
