"""Requests are checked against the metric's contract before any SQL exists.

Every refusal carries a stable code, the offending field and value, a remediation, and the
authorised alternatives — so the agent's next attempt is a lookup rather than a guess.
"""

from pathlib import Path

import pytest

from metricbridge.contract import QueryRequest, RefusalError, validate
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
