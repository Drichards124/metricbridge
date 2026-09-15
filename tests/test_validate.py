"""Requests are checked against the metric's contract before any SQL exists.

Every refusal carries a stable code, the offending field and value, a remediation, and the
authorised alternatives — so the agent's next attempt is a lookup rather than a guess.
"""

import textwrap
from pathlib import Path

import pytest

from metricbridge.contract import QueryRequest, RefusalError, signature, validate
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"
Q3 = {"start_date": "2026-07-01", "end_date": "2026-09-30"}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


def request(**overrides) -> QueryRequest:
    return QueryRequest(**{"metric": "revenue", "date_range": Q3, **overrides})


def refusals(manifest, **overrides):
    with pytest.raises(RefusalError) as refused:
        validate(manifest, request(**overrides))
    return refused.value.refusals


def codes(manifest, **overrides):
    return sorted(r.code for r in refusals(manifest, **overrides))


class TestResolution:
    def test_bare_dimension_of_the_base_model(self, manifest):
        resolved = validate(manifest, request(dimensions=["channel"]))
        assert [(d.requested, d.model, d.column) for d in resolved.dimensions] == [
            ("channel", "orders", "channel")
        ]

    def test_bare_dimension_reached_through_a_join(self, manifest):
        resolved = validate(manifest, request(dimensions=["segment"]))
        assert [(d.requested, d.model, d.entity) for d in resolved.dimensions] == [
            ("segment", "customers", "customer")
        ]

    def test_qualified_dimension_is_always_accepted(self, manifest):
        resolved = validate(manifest, request(dimensions=["customer__region"]))
        assert [(d.requested, d.model, d.column) for d in resolved.dimensions] == [
            ("customer__region", "customers", "region")
        ]

    def test_filters_split_by_what_the_field_is(self, manifest):
        resolved = validate(
            manifest,
            request(
                dimensions=["channel"],
                filters=[
                    {"field": "channel", "operator": "=", "value": "web"},
                    {"field": "revenue", "operator": ">", "value": 50000},
                ],
            ),
        )
        assert [(f.field, f.operator) for f in resolved.where_filters] == [("channel", "=")]
        assert [(f.field, f.operator) for f in resolved.having_filters] == [("revenue", ">")]

    def test_dates_are_parsed_and_the_partition_is_named(self, manifest):
        resolved = validate(manifest, request())
        assert resolved.date_range.start_date.isoformat() == "2026-07-01"
        assert resolved.date_range.end_date.isoformat() == "2026-09-30"
        assert resolved.partition.name == "order_date"

    def test_declared_snapshot_rollup_is_admitted(self, manifest):
        resolved = validate(manifest, request(metric="inventory_on_hand", time_grain="month"))
        assert resolved.snapshot is not None
        assert (resolved.snapshot.name, resolved.snapshot.window_choice) == ("snapshot_date", "max")

    def test_deprecated_metric_answers_with_a_notice(self, manifest):
        resolved = validate(manifest, request(metric="gross_revenue"))
        assert any("deprecated" in n.lower() and "revenue" in n for n in resolved.notices)

    def test_ratio_metric_resolves_both_legs(self, manifest):
        resolved = validate(manifest, request(metric="average_order_value", dimensions=["channel"]))
        assert resolved.metric.type == "ratio"
        assert [d.model for d in resolved.dimensions] == ["orders"]


class TestRefusals:
    def test_unknown_metric_suggests_near_names(self, manifest):
        (refusal,) = refusals(manifest, metric="revenu")
        assert refusal.code == "unknown_metric"
        assert refusal.field == "metric"
        assert refusal.offending_value == "revenu"
        assert "revenue" in refusal.valid_alternatives
        assert refusal.remediation

    def test_unknown_dimension_lists_authorised_cuts(self, manifest):
        (refusal,) = refusals(manifest, dimensions=["regionn"])
        assert refusal.code == "unknown_dimension"
        assert refusal.field == "dimensions"
        assert "customer__region" in refusal.valid_alternatives

    def test_ambiguous_bare_dimension_lists_qualified_names(self, manifest):
        (refusal,) = refusals(manifest, dimensions=["region"])
        assert refusal.code == "ambiguous_dimension"
        assert sorted(refusal.valid_alternatives) == ["customer__region", "product__region"]

    def test_unsupported_time_grain(self, manifest):
        (refusal,) = refusals(manifest, time_grain="hour")
        assert refusal.code == "unsupported_time_grain"
        assert "day" in refusal.valid_alternatives

    def test_missing_date_range(self, manifest):
        with pytest.raises(RefusalError) as refused:
            validate(manifest, QueryRequest(metric="revenue"))
        (refusal,) = refused.value.refusals
        assert refusal.code == "missing_partition_filter"
        assert "order_date" in refusal.message

    def test_reversed_date_range(self, manifest):
        (refusal,) = refusals(
            manifest, date_range={"start_date": "2026-09-30", "end_date": "2026-07-01"}
        )
        assert refusal.code == "invalid_date_range"

    def test_unparseable_date(self, manifest):
        (refusal,) = refusals(
            manifest, date_range={"start_date": "last tuesday", "end_date": "2026-07-01"}
        )
        assert refusal.code == "invalid_date_range"
        assert refusal.offending_value == "last tuesday"

    def test_window_wider_than_the_metric_allows(self, manifest):
        (refusal,) = refusals(
            manifest, date_range={"start_date": "2024-01-01", "end_date": "2026-09-30"}
        )
        assert refusal.code == "partition_window_too_wide"
        assert "400" in refusal.remediation

    @pytest.mark.parametrize("grain", [None, "month"])
    def test_snapshot_without_a_declared_rollup_is_refused(self, manifest, grain):
        (refusal,) = refusals(manifest, metric="stock_level", time_grain=grain)
        assert refusal.code == "non_additive_cut"
        assert "day" in refusal.valid_alternatives
        assert refusal.remediation

    def test_snapshot_at_its_base_grain_is_allowed(self, manifest):
        assert validate(manifest, request(metric="stock_level", time_grain="day")).snapshot is None

    def test_unknown_filter_field(self, manifest):
        (refusal,) = refusals(
            manifest, filters=[{"field": "colour", "operator": "=", "value": "red"}]
        )
        assert refusal.code == "unknown_filter_field"

    def test_unsupported_operator(self, manifest):
        (refusal,) = refusals(
            manifest, filters=[{"field": "channel", "operator": "~=", "value": "web"}]
        )
        assert refusal.code == "unsupported_operator"
        assert "=" in refusal.valid_alternatives

    def test_order_by_a_field_not_in_the_result(self, manifest):
        (refusal,) = refusals(manifest, order_by=[{"field": "segment", "direction": "desc"}])
        assert refusal.code == "unknown_order_field"
        assert "revenue" in refusal.valid_alternatives

    def test_row_limit_above_the_ceiling(self, manifest):
        (refusal,) = refusals(manifest, row_limit=10_000)
        assert refusal.code == "row_limit_exceeded"
        assert "1000" in refusal.message

    def test_every_problem_is_reported_at_once(self, manifest):
        assert codes(
            manifest,
            dimensions=["regionn"],
            time_grain="hour",
            row_limit=10_000,
            filters=[{"field": "colour", "operator": "=", "value": "red"}],
        ) == [
            "row_limit_exceeded",
            "unknown_dimension",
            "unknown_filter_field",
            "unsupported_time_grain",
        ]

    def test_the_payload_is_machine_readable(self, manifest):
        with pytest.raises(RefusalError) as refused:
            validate(manifest, request(dimensions=["regionn"]))
        payload = refused.value.payload()
        assert payload["ok"] is False
        assert payload["errors"][0]["code"] == "unknown_dimension"
        assert "valid_alternatives" in payload["errors"][0]


WEEKLY = textwrap.dedent("""\
    semantic_models:
      - name: weekly_sales
        table: shop.weekly_sales
        entities:
          - {name: week_row, type: primary, expr: id}
        dimensions:
          - {name: week_start, type: time, time_granularity: week, is_partition: true}
        measures:
          - {name: weekly_revenue, agg: sum, expr: amount}
    metrics:
      - {name: weekly_revenue, type: simple, measure: weekly_revenue, description: Weekly revenue.}
    """)


def test_a_grain_finer_than_the_table_is_refused(tmp_path):
    """A weekly table cannot answer a daily question: those rows do not exist."""
    (tmp_path / "weekly.yml").write_text(WEEKLY)
    weekly = load_manifest(tmp_path)

    with pytest.raises(RefusalError) as refused:
        validate(weekly, QueryRequest(metric="weekly_revenue", date_range=Q3, time_grain="day"))
    (refusal,) = refused.value.refusals
    assert refusal.code == "unsupported_time_grain"
    assert refusal.valid_alternatives == ["week", "month", "quarter", "year"]
    assert "week" in refusal.message

    assert signature(weekly, "weekly_revenue")["time_grains"] == [
        "week",
        "month",
        "quarter",
        "year",
    ]
    assert validate(
        weekly, QueryRequest(metric="weekly_revenue", date_range=Q3, time_grain="quarter")
    )


def test_unknown_filter_field_offers_real_fields_when_nothing_is_close(manifest):
    (refusal,) = refusals(manifest, filters=[{"field": "colour", "operator": "=", "value": "red"}])
    assert refusal.code == "unknown_filter_field"
    # Ordered as the model reads: the metric, then its own cuts, then each join's.
    assert refusal.valid_alternatives == [
        "revenue",
        "channel",
        "created_at",
        "order_date",
        "customer__created_at",
        "customer__region",
        "customer__segment",
        "product__category",
        "product__region",
    ]


def test_alternatives_are_capped_on_a_wide_table(tmp_path):
    """A 200-cut table must not answer a typo with 200 names."""
    dimensions = "\n".join(f"      - {{name: cut_{i:03d}, type: categorical}}" for i in range(200))
    (tmp_path / "wide.yml").write_text(
        "semantic_models:\n"
        "  - name: wide\n"
        "    table: shop.wide\n"
        "    entities:\n"
        "      - {name: row_id, type: primary, expr: id}\n"
        "    dimensions:\n"
        "      - {name: day, type: time, time_granularity: day, is_partition: true}\n"
        f"{dimensions}\n"
        "    measures:\n"
        "      - {name: total, agg: sum, expr: amount}\n"
        "metrics:\n"
        "  - {name: total, type: simple, measure: total, description: Total.}\n"
    )
    wide = load_manifest(tmp_path)
    with pytest.raises(RefusalError) as refused:
        validate(wide, QueryRequest(metric="total", date_range=Q3, dimensions=["nothing_like_it"]))
    (refusal,) = refused.value.refusals
    assert len(refusal.valid_alternatives) == 25


class TestMetricFilters:
    def test_a_metric_carries_its_own_filter(self, manifest):
        resolved = validate(manifest, request(metric="web_revenue"))
        (applied,) = resolved.metric_filters
        assert (applied.field, applied.operator, applied.value) == ("channel", "=", "web")
        assert applied.dimension.model == "orders"
        assert applied.dimension.column == "channel"
        assert resolved.where_filters == []

    def test_a_metric_filter_can_reach_through_a_join(self, manifest):
        resolved = validate(manifest, request(metric="enterprise_revenue"))
        (applied,) = resolved.metric_filters
        assert applied.dimension.model == "customers"
        assert applied.dimension.entity == "customer"

    def test_metric_and_request_filters_stay_distinguishable(self, manifest):
        resolved = validate(
            manifest,
            request(
                metric="web_revenue",
                filters=[{"field": "customer__region", "operator": "=", "value": "emea"}],
            ),
        )
        assert [f.field for f in resolved.metric_filters] == ["channel"]
        assert [f.field for f in resolved.where_filters] == ["customer__region"]


class TestDefinitionConstraints:
    """A definition that pins a field makes that field not a cut of the metric at all."""

    def test_grouping_by_a_pinned_field_is_refused(self, manifest):
        (refusal,) = refusals(manifest, metric="web_revenue", dimensions=["channel"])
        assert refusal.code == "fixed_by_definition"
        assert refusal.field == "dimensions"
        assert "revenue" in refusal.valid_alternatives
        assert "web" in refusal.message

    def test_filtering_a_pinned_field_is_refused(self, manifest):
        (refusal,) = refusals(
            manifest,
            metric="web_revenue",
            filters=[{"field": "channel", "operator": "=", "value": "store"}],
        )
        assert refusal.code == "fixed_by_definition"
        assert refusal.field == "filters"
        assert refusal.remediation.startswith("Ask 'revenue'")

    def test_a_filter_that_can_never_match_is_refused(self, manifest):
        """The refunds shape: asking a metric for what its definition excludes."""
        (refusal,) = refusals(
            manifest,
            metric="enterprise_revenue",
            filters=[{"field": "customer__segment", "operator": "=", "value": "smb"}],
        )
        assert refusal.code == "contradictory_filter"
        assert "empty result" in refusal.message
        assert "revenue" in refusal.valid_alternatives

    def test_narrowing_within_a_constraint_is_allowed(self, manifest):
        resolved = validate(
            manifest,
            request(
                metric="enterprise_revenue",
                filters=[{"field": "customer__segment", "operator": "=", "value": "enterprise"}],
            ),
        )
        assert [f.field for f in resolved.where_filters] == ["customer__segment"]

    def test_grouping_by_a_constrained_but_unpinned_field_is_allowed(self, manifest):
        resolved = validate(
            manifest, request(metric="enterprise_revenue", dimensions=["customer__segment"])
        )
        assert [d.model for d in resolved.dimensions] == ["customers"]

    def test_an_unrelated_field_is_unaffected(self, manifest):
        resolved = validate(
            manifest,
            request(
                metric="web_revenue",
                filters=[{"field": "customer__region", "operator": "=", "value": "emea"}],
            ),
        )
        assert [f.field for f in resolved.where_filters] == ["customer__region"]


# Order lines join to orders, a table with its own partition column, and to products, which has
# none. The compiled join to orders could not bound that table's scan on its own date without
# dropping matching lines, so the guardrail would refuse it: its cuts are not offered at all.
PARTITIONED_JOIN = textwrap.dedent("""\
    semantic_models:
      - name: order_lines
        table: shop.order_lines
        entities:
          - {name: line, type: primary, expr: line_id}
          - {name: order, type: foreign, expr: order_id}
          - {name: product, type: foreign, expr: product_id}
        dimensions:
          - {name: ship_date, type: time, time_granularity: day, is_partition: true}
        measures:
          - {name: line_revenue, agg: sum, expr: amount}
      - name: orders
        table: shop.orders
        entities:
          - {name: order, type: primary, expr: order_id}
        dimensions:
          - {name: order_date, type: time, time_granularity: day, is_partition: true}
          - {name: priority, type: categorical}
        measures:
          - {name: order_total, agg: sum, expr: total}
      - name: products
        table: shop.products
        entities:
          - {name: product, type: primary, expr: product_id}
        dimensions:
          - {name: brand, type: categorical}
    metrics:
      - {name: line_revenue, type: simple, measure: line_revenue, description: Line revenue.}
    """)


class TestCutsThroughAPartitionedModel:
    @pytest.fixture
    def lines(self, tmp_path):
        (tmp_path / "lines.yml").write_text(PARTITIONED_JOIN)
        return load_manifest(tmp_path)

    def refused(self, lines, **overrides):
        with pytest.raises(RefusalError) as refused:
            validate(lines, QueryRequest(metric="line_revenue", date_range=Q3, **overrides))
        (refusal,) = refused.value.refusals
        return refusal

    def test_the_signature_does_not_offer_them(self, lines):
        sig = signature(lines, "line_revenue")
        assert [d["name"] for d in sig["dimensions"]] == ["ship_date", "product__brand"]
        assert "order__priority" not in sig["filters"]["fields"]

    @pytest.mark.parametrize("requested", ["order__priority", "priority", "order__order_date"])
    def test_a_request_for_one_is_refused_as_an_unknown_dimension(self, lines, requested):
        refusal = self.refused(lines, dimensions=[requested])
        assert refusal.code == "unknown_dimension"
        assert refusal.valid_alternatives == ["ship_date", "product__brand"]

    def test_a_filter_on_one_is_refused_as_an_unknown_field(self, lines):
        refusal = self.refused(
            lines, filters=[{"field": "order__priority", "operator": "=", "value": "1-URGENT"}]
        )
        assert refusal.code == "unknown_filter_field"
        assert "order__priority" not in refusal.valid_alternatives
        assert "product__brand" in refusal.valid_alternatives

    def test_every_cut_the_signature_offers_compiles_to_a_statement_the_guardrail_accepts(
        self, lines
    ):
        """The symmetry rule end to end: offered means answerable, not refused after compiling."""
        from metricbridge.compiler import compile_query
        from metricbridge.guardrail import assert_safe

        for cut in signature(lines, "line_revenue")["dimensions"]:
            request = QueryRequest(metric="line_revenue", date_range=Q3, dimensions=[cut["name"]])
            assert_safe(compile_query(lines, validate(lines, request)).sql, lines)
