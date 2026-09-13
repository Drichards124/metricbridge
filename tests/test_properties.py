"""Invariants over generated manifests, not just the ones we thought to write.

The grain bug found on 13 Sep — a weekly table would have answered a daily question — survived a
symmetry test because every fixture was daily-partitioned. Examples prove a case; these prove a
property, so the next such bug fails here rather than in a warehouse.
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metricbridge.contract import QueryRequest, RefusalError, signature, validate
from metricbridge.contract.context import GRAIN_ORDER
from metricbridge.manifest import (
    Dimension,
    Entity,
    Measure,
    Metric,
    MetricFilter,
    NonAdditiveDimension,
    SemanticModel,
    assemble,
)

RANGE = {"start_date": "2026-07-01", "end_date": "2026-09-30"}
CUTS = ["colour", "size", "d_date", "nope", "customer__region"]


@st.composite
def manifests(draw):
    """A one-table manifest whose shape varies where our rules do: grain, additivity, pinning."""
    grain = draw(st.sampled_from(GRAIN_ORDER))
    additive = draw(st.booleans())
    declared = draw(st.booleans()) if not additive else False
    pinned = draw(st.booleans())

    model = SemanticModel(
        name="facts",
        table="s.facts",
        entities=[Entity(name="row", type="primary", expr="id")],
        dimensions=[
            Dimension(
                name="d_date", type="time", expr="d", time_granularity=grain, is_partition=True
            ),
            Dimension(name="colour", type="categorical", expr="colour"),
            Dimension(name="size", type="categorical", expr="size"),
        ],
        measures=[
            Measure(
                name="m",
                agg="sum",
                expr="amount",
                additive=additive,
                non_additive_dimension=(
                    NonAdditiveDimension(name="d_date", window_choice="max") if declared else None
                ),
            )
        ],
    )
    metric = Metric(
        name="metric",
        type="simple",
        measure="m",
        description="generated",
        tier="certified",
        filters=[MetricFilter(field="colour", operator="=", value="red")] if pinned else [],
    )
    return assemble({model.name: model}, {metric.name: metric})


def accepted(manifest, **overrides) -> bool:
    """Ask as an agent that read the signature would: honour what it declares as required."""
    sig = signature(manifest, "metric")
    if sig["time_grain_required"]:
        overrides.setdefault("time_grain", sig["time_grains"][0])
    try:
        validate(manifest, QueryRequest(metric="metric", date_range=RANGE, **overrides))
    except RefusalError:
        return False
    return True


settings.register_profile(
    "contract", suppress_health_check=[HealthCheck.too_slow], max_examples=150
)
settings.load_profile("contract")


@given(manifests(), st.sampled_from(GRAIN_ORDER))
def test_a_grain_is_accepted_exactly_when_the_signature_offers_it(manifest, grain):
    offered = grain in signature(manifest, "metric")["time_grains"]
    assert accepted(manifest, time_grain=grain) is offered


@given(manifests())
def test_a_metric_that_demands_a_grain_says_so(manifest):
    """Otherwise an agent that read the signature is still refused — the gap these tests found."""
    sig = signature(manifest, "metric")
    if sig["time_grain_required"]:
        assert not accepted(manifest, time_grain=None)
        assert sig["time_grains"] == [sig["time_grains"][0]]


@given(manifests(), st.sampled_from(CUTS))
def test_a_cut_is_accepted_exactly_when_the_signature_offers_it(manifest, cut):
    offered = cut in {d["name"] for d in signature(manifest, "metric")["dimensions"]}
    assert accepted(manifest, dimensions=[cut]) is offered


@given(manifests(), st.sampled_from(CUTS))
def test_a_filter_field_is_accepted_exactly_when_the_signature_offers_it(manifest, field):
    offered = field in signature(manifest, "metric")["filters"]["fields"]
    request = {"field": field, "operator": "=", "value": "x"}
    assert accepted(manifest, filters=[request]) is offered


@given(manifests())
def test_every_refusal_names_a_known_code_and_a_way_forward(manifest):
    try:
        validate(
            manifest,
            QueryRequest(
                metric="metric",
                date_range=RANGE,
                dimensions=["nope"],
                time_grain="year",
                filters=[{"field": "nope", "operator": "=", "value": "x"}],
            ),
        )
    except RefusalError as refused:
        from metricbridge.contract.rules import CODES

        for refusal in refused.refusals:
            assert refusal.code in CODES
            assert refusal.message
            assert refusal.remediation


@given(manifests())
def test_what_the_signature_offers_can_always_be_asked_for(manifest):
    """The promise in one line: nothing advertised is refused."""
    sig = signature(manifest, "metric")
    for grain in sig["time_grains"]:
        assert accepted(manifest, time_grain=grain)
    for cut in (d["name"] for d in sig["dimensions"]):
        assert accepted(manifest, dimensions=[cut])
