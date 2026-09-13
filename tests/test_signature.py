"""The signature is the exhaustive contract for one metric.

The symmetry rule from the design: every constraint the validator enforces must be discoverable in
advance. Here that is mechanical — a rule declares its check and its signature entry together, and
these tests fail if the two ever drift apart.
"""

from pathlib import Path

import pytest

from metricbridge.contract import signature, validate
from metricbridge.contract.rules import CODES, RULES
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(STOREFRONT)


def test_signature_describes_the_metric(manifest):
    sig = signature(manifest, "revenue")
    assert sig["metric"] == "revenue"
    assert sig["type"] == "simple"
    assert sig["tier"] == "certified"
    assert sig["owner"] == "finance"
    assert sig["description"]
    assert sig["additive"] is True


def test_signature_lists_every_authorised_cut_with_its_qualified_name(manifest):
    dimensions = {d["name"]: d for d in signature(manifest, "revenue")["dimensions"]}
    assert dimensions["channel"]["model"] == "orders"
    assert dimensions["customer__region"]["entity"] == "customer"
    # `region` is ambiguous across two joined models, so only the qualified names are offered.
    assert "region" not in dimensions
    assert {"customer__region", "product__region"} <= set(dimensions)


def test_signature_states_the_mandatory_date_bound_and_its_cap(manifest):
    required = signature(manifest, "revenue")["required_filters"][0]
    assert required["field"] == "order_date"
    assert required["shape"] == "date_range {start_date, end_date}"
    assert required["max_window_days"] == 400


def test_signature_states_grains_filters_ordering_and_limits(manifest):
    sig = signature(manifest, "revenue")
    assert sig["time_grains"] == ["day", "week", "month", "quarter", "year"]
    assert "=" in sig["filters"]["operators"]
    assert "revenue" in sig["filters"]["fields"]
    assert set(sig["order_by"]["directions"]) == {"asc", "desc"}
    assert sig["row_limit"] == {"default": 100, "maximum": 1000}


def test_signature_warns_about_a_deprecated_metric(manifest):
    sig = signature(manifest, "gross_revenue")
    assert sig["tier"] == "deprecated"
    assert sig["replaced_by"] == "revenue"


def test_signature_declares_how_a_snapshot_rolls_up(manifest):
    declared = signature(manifest, "inventory_on_hand")
    assert declared["additive"] is False
    assert declared["non_additive_dimension"]["window_choice"] == "max"
    assert declared["time_grains"] == ["day", "week", "month", "quarter", "year"]

    undeclared = signature(manifest, "stock_level")
    assert undeclared["additive"] is False
    assert undeclared["non_additive_dimension"] is None
    # Nothing says how it rolls up, so only its base grain is offered — and that is what
    # `validate` enforces, which is the symmetry rule holding for this metric.
    assert undeclared["time_grains"] == ["day"]


def test_unknown_metric_has_no_signature(manifest):
    with pytest.raises(KeyError):
        signature(manifest, "revenu")


@pytest.mark.parametrize(
    "metric", ["revenue", "average_order_value", "trailing_12m_revenue", "stock_level"]
)
def test_every_rule_contributes_to_the_signature(manifest, metric):
    """A rule that enforces something the signature never mentions is a broken promise."""
    sig = signature(manifest, metric)
    for rule in RULES:
        assert rule.signature_keys, f"rule {rule.name} declares no signature entry"
        missing = [key for key in rule.signature_keys if key not in sig]
        assert not missing, f"rule {rule.name} enforces {missing}, absent from the signature"


def test_every_refusal_code_belongs_to_a_registered_rule():
    declared = {code for rule in RULES for code in rule.codes}
    assert declared <= CODES
    assert CODES - declared == {"unknown_metric"}  # raised before any rule can run


def test_cumulative_metric_signature_states_its_window(manifest):
    sig = signature(manifest, "trailing_12m_revenue")
    assert sig["type"] == "cumulative"
    assert sig["window"] == "12 months"


def test_signature_matches_what_validate_accepts(manifest):
    """Spot-check the promise: everything the signature offers for this metric is accepted."""
    from metricbridge.contract import QueryRequest

    sig = signature(manifest, "revenue")
    for dimension in (d["name"] for d in sig["dimensions"]):
        validate(
            manifest,
            QueryRequest(
                metric="revenue",
                date_range={"start_date": "2026-07-01", "end_date": "2026-09-30"},
                dimensions=[dimension],
            ),
        )
    for grain in sig["time_grains"]:
        validate(
            manifest,
            QueryRequest(
                metric="revenue",
                date_range={"start_date": "2026-07-01", "end_date": "2026-09-30"},
                time_grain=grain,
            ),
        )
