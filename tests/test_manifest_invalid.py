"""Every invalid manifest is refused at load time with all of its problems, each located."""

import textwrap
from pathlib import Path

import pytest

from metricbridge.manifest import ManifestError, load_manifest

MINIMAL = textwrap.dedent("""\
    semantic_models:
      - name: orders
        table: shop.orders
        entities:
          - {name: order, type: primary, expr: order_id}
        dimensions:
          - {name: order_date, type: time, time_granularity: day, is_partition: true}
          - {name: channel, type: categorical}
        measures:
          - {name: revenue, agg: sum, expr: amount}
    metrics:
      - {name: revenue, type: simple, measure: revenue, description: Revenue.}
    """)


def edit(old: str, new: str, text: str = MINIMAL) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def extra_model(name: str, body: str) -> str:
    header = f"semantic_models:\n  - name: {name}\n    table: shop.{name}\n"
    return header + textwrap.indent(textwrap.dedent(body), "    ")


CASES = [
    pytest.param(
        {"m.yml": "metrics: [unclosed\n"},
        [("m.yml", "", "invalid yaml")],
        id="yaml-syntax",
    ),
    pytest.param(
        {"m.yml": "- a\n- list\n"},
        [("m.yml", "", "must be a mapping")],
        id="top-level-not-a-mapping",
    ),
    pytest.param(
        {"m.yml": edit("agg: sum", "aggr: sum")},
        [
            ("m.yml", "semantic_models[0].measures[0].aggr", "unknown field"),
            ("m.yml", "semantic_models[0].measures[0].agg", "required"),
        ],
        id="unknown-key-and-missing-key-both-reported",
    ),
    pytest.param(
        {"m.yml": edit("agg: sum", "agg: percentile")},
        [("m.yml", "semantic_models[0].measures[0].agg", "not supported in this version")],
        id="deferred-aggregation",
    ),
    pytest.param(
        {"m.yml": edit("agg: sum", "agg: total")},
        [("m.yml", "semantic_models[0].measures[0].agg", "unknown aggregation")],
        id="unknown-aggregation",
    ),
    pytest.param(
        {"m.yml": edit("type: simple, measure: revenue", "type: derived")},
        [("m.yml", "metrics[0].type", "not supported in this version")],
        id="deferred-metric-type",
    ),
    pytest.param(
        {"m.yml": edit("time_granularity: day", "time_granularity: hour")},
        [
            (
                "m.yml",
                "semantic_models[0].dimensions[0].time_granularity",
                "not supported in this version",
            )
        ],
        id="sub-day-granularity",
    ),
    pytest.param(
        {"m.yml": edit("type: primary", "type: natural")},
        [("m.yml", "semantic_models[0].entities[0].type", "not supported in this version")],
        id="natural-entity",
    ),
    pytest.param(
        {"m.yml": edit("description: Revenue.}", "description: Revenue., max_window_days: 0}")},
        [("m.yml", "metrics[0].max_window_days", "greater than 0")],
        id="non-positive-window-cap",
    ),
    pytest.param(
        {"m.yml": edit("measure: revenue, description: Revenue.}", "measure: revenue}")},
        [("m.yml", "metrics[0].description", "required")],
        id="metric-without-description",
    ),
    pytest.param(
        {"m.yml": edit("time_granularity: day, ", "")},
        [
            (
                "m.yml",
                "semantic_models[0].dimensions[0].time_granularity",
                "required for time dimensions",
            )
        ],
        id="time-dimension-without-granularity",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "{name: channel, type: categorical}",
                "{name: channel, type: categorical, is_partition: true}",
            )
        },
        [("m.yml", "semantic_models[0].dimensions[1].is_partition", "only time dimensions")],
        id="partition-on-categorical",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "type: simple, measure: revenue,",
                "type: simple, measure: revenue, numerator: revenue,",
            )
        },
        [("m.yml", "metrics[0].numerator", "not allowed for simple metrics")],
        id="simple-metric-with-ratio-field",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "type: simple, measure: revenue,",
                "type: cumulative, measure: revenue, window: 12 months, grain_to_date: month,",
            )
        },
        [("m.yml", "metrics[0]", "exactly one of window or grain_to_date")],
        id="cumulative-with-both-window-kinds",
    ),
    pytest.param(
        {"m.yml": edit("type: simple, measure: revenue,", "type: cumulative, measure: revenue,")},
        [("m.yml", "metrics[0]", "exactly one of window or grain_to_date")],
        id="cumulative-with-no-window",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "type: simple, measure: revenue,",
                "type: cumulative, measure: revenue, window: twelve months,",
            )
        },
        [("m.yml", "metrics[0].window", "invalid window")],
        id="unparseable-window",
    ),
    pytest.param(
        {
            "a.yml": MINIMAL,
            "b.yml": "metrics:\n  - {name: revenue, type: simple, measure: revenue, description: Again.}\n",
        },
        [("b.yml", "metrics[0].name", "defined more than once (first in a.yml)")],
        id="duplicate-metric-across-files",
    ),
    pytest.param(
        {
            "a.yml": MINIMAL,
            "b.yml": extra_model(
                "refunds",
                """\
                dimensions:
                  - {name: refund_date, type: time, time_granularity: day, is_partition: true}
                measures:
                  - {name: revenue, agg: sum, expr: refunded}
                """,
            ),
        },
        [
            (
                "b.yml",
                "semantic_models[0].measures[0].name",
                "measure 'revenue' is defined more than once",
            )
        ],
        id="duplicate-measure-across-models",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "  - {name: channel, type: categorical}",
                "  - {name: channel, type: categorical}\n      - {name: channel, type: categorical}",
            )
        },
        [("m.yml", "semantic_models[0].dimensions[2].name", "defined more than once")],
        id="duplicate-dimension-in-model",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "{name: order, type: primary, expr: order_id}",
                "{name: order, type: primary, expr: order_id}\n      - {name: order_line, type: primary}",
            )
        },
        [("m.yml", "semantic_models[0].entities", "more than one primary entity")],
        id="two-primary-entities",
    ),
    pytest.param(
        {"m.yml": edit(", is_partition: true", "")},
        [("m.yml", "semantic_models[0].dimensions", "no partition dimension")],
        id="missing-partition",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "  - {name: channel, type: categorical}",
                "  - {name: channel, type: categorical}\n      - {name: shipped_date, type: time, time_granularity: day, is_partition: true}",
            )
        },
        [("m.yml", "semantic_models[0].dimensions", "more than one partition dimension")],
        id="two-partitions",
    ),
    pytest.param(
        {"m.yml": edit("measure: revenue,", "measure: sales,")},
        [("m.yml", "metrics[0].measure", "unknown measure 'sales'")],
        id="unknown-measure",
    ),
    pytest.param(
        {
            "m.yml": MINIMAL
            + "  - {name: aov, type: ratio, numerator: revenue, denominator: orders, description: AOV.}\n"
        },
        [("m.yml", "metrics[1].denominator", "unknown metric 'orders'")],
        id="ratio-with-unknown-metric",
    ),
    pytest.param(
        {
            "m.yml": MINIMAL
            + "  - {name: share, type: ratio, numerator: revenue, denominator: revenue, description: Share.}\n"
            + "  - {name: share_of_share, type: ratio, numerator: share, denominator: revenue, description: Nested.}\n"
        },
        [("m.yml", "metrics[2].numerator", "must be a simple or cumulative metric")],
        id="ratio-of-ratio",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "description: Revenue.}",
                'description: Revenue., filters: [{field: colour, operator: "=", value: red}]}',
            )
        },
        [("m.yml", "metrics[0].filters[0].field", "not an authorised cut")],
        id="metric-filter-on-unknown-field",
    ),
    pytest.param(
        {
            "m.yml": MINIMAL,
            "l.yml": extra_model(
                "order_lines",
                """\
                entities:
                  - {name: line, type: primary, expr: line_id}
                  - {name: order, type: foreign, expr: order_id}
                dimensions:
                  - {name: ship_date, type: time, time_granularity: day, is_partition: true}
                measures:
                  - {name: line_revenue, agg: sum, expr: amount}
                """,
            )
            + 'metrics:\n  - {name: web_line_revenue, type: simple, measure: line_revenue, description: Web lines., filters: [{field: order__channel, operator: "=", value: web}]}\n',
        },
        # orders has its own partition column, so its cuts are not reachable from order lines.
        [("l.yml", "metrics[0].filters[0].field", "not an authorised cut")],
        id="metric-filter-through-a-join-to-a-partitioned-model",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "description: Revenue.}",
                'description: Revenue., filters: [{field: channel, operator: "~=", value: web}]}',
            )
        },
        [("m.yml", "metrics[0].filters[0].operator", "unknown operator")],
        id="metric-filter-with-unknown-operator",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "{name: revenue, agg: sum, expr: amount}",
                "{name: revenue, agg: sum, expr: amount, additive: false, non_additive_dimension: {name: channel, window_choice: max}}",
            )
        },
        [
            (
                "m.yml",
                "semantic_models[0].measures[0].non_additive_dimension.name",
                "must be a time dimension",
            )
        ],
        id="non-additive-on-categorical",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "{name: revenue, agg: sum, expr: amount}",
                "{name: revenue, agg: sum, expr: amount, additive: false, non_additive_dimension: {name: order_date, window_choice: max, window_groupings: [store]}}",
            )
        },
        [
            (
                "m.yml",
                "semantic_models[0].measures[0].non_additive_dimension.window_groupings[0]",
                "unknown entity 'store'",
            )
        ],
        id="non-additive-grouping-unknown-entity",
    ),
    pytest.param(
        {
            "m.yml": edit(
                "{name: revenue, agg: sum, expr: amount}",
                "{name: revenue, agg: sum, expr: amount, non_additive_dimension: {name: order_date, window_choice: max}}",
            )
        },
        [
            (
                "m.yml",
                "semantic_models[0].measures[0].non_additive_dimension",
                "must set additive: false",
            )
        ],
        id="rollup-declared-without-additive-false",
    ),
    pytest.param(
        {
            "a.yml": extra_model(
                "orders",
                """\
                entities:
                  - {name: customer, type: foreign, expr: customer_id}
                dimensions:
                  - {name: order_date, type: time, time_granularity: day, is_partition: true}
                measures:
                  - {name: revenue, agg: sum, expr: amount}
                """,
            ),
            "b.yml": extra_model(
                "tickets",
                """\
                entities:
                  - {name: customer, type: foreign, expr: customer_id}
                dimensions:
                  - {name: opened_date, type: time, time_granularity: day, is_partition: true}
                measures:
                  - {name: ticket_count, agg: count, expr: ticket_id}
                """,
            ),
        },
        [("b.yml", "semantic_models[0].entities[0]", "many-to-many")],
        id="many-to-many-through-shared-foreign-entity",
    ),
    pytest.param(
        {"m.yml": edit("measure: revenue,", "measure: sales,", edit(", is_partition: true", ""))},
        [
            ("m.yml", "semantic_models[0].dimensions", "no partition dimension"),
            ("m.yml", "metrics[0].measure", "unknown measure 'sales'"),
        ],
        id="several-semantic-problems-all-reported",
    ),
]


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).write_text(text)


@pytest.mark.parametrize(("files", "expected"), CASES)
def test_invalid_manifest_is_refused_with_every_problem_located(tmp_path, files, expected):
    _write(tmp_path, files)
    with pytest.raises(ManifestError) as refused:
        load_manifest(tmp_path)
    issues = refused.value.issues
    assert sorted((i.file, i.path) for i in issues) == sorted((f, p) for f, p, _ in expected)
    for file, path, fragment in expected:
        message = next(i.message for i in issues if (i.file, i.path) == (file, path))
        assert fragment.lower() in message.lower(), f"{file}:{path}: {message!r}"


def test_yaml_syntax_error_reports_the_line(tmp_path):
    _write(tmp_path, {"m.yml": "metrics:\n  - {name: revenue\n"})
    with pytest.raises(ManifestError) as refused:
        load_manifest(tmp_path)
    assert "line" in refused.value.issues[0].message


def test_directory_without_manifest_files_is_refused(tmp_path):
    with pytest.raises(ManifestError) as refused:
        load_manifest(tmp_path)
    assert [(i.file, i.path) for i in refused.value.issues] == [("", "")]
    assert "no manifest files" in refused.value.issues[0].message
