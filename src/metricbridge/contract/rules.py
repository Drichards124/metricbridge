"""The rule registry: one place where a constraint declares both its check and how it is advertised.

The symmetry rule from the design — every constraint the validator enforces must be discoverable in
advance — is the thing most likely to rot as features are added. Here it cannot: a rule carries the
signature keys it speaks for, and a test walks this registry and fails when one of them is missing
from the signature. Enforcing something the agent cannot look up first is a broken promise.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ..manifest import OPERATORS
from .context import GRAIN_ORDER, Context, close_matches
from .errors import Refusal
from .request import MAX_ROW_LIMIT


@dataclass(frozen=True)
class Rule:
    name: str
    codes: tuple[str, ...]
    signature_keys: tuple[str, ...]
    check: Callable[[Context], list[Refusal]]


def _metric_shape(context: Context) -> list[Refusal]:
    if not context.mixed_models:
        return []
    return [
        Refusal(
            code="unsupported_metric_shape",
            message=(
                f"{context.metric.name!r} combines measures from more than one table, "
                "which this version cannot compile."
            ),
            field="metric",
            offending_value=context.metric.name,
            remediation="Query each side separately, or define the metric over a single table.",
        )
    ]


def _definition_constraints(context: Context) -> list[Refusal]:
    """A field the definition pins is not a cut of this metric, and a filter that can never match
    is a question this metric cannot answer — both are better refused than answered with zero."""
    siblings = context.unfiltered_siblings
    where_else = (
        f"Ask {siblings[0]!r} instead, which leaves that field free."
        if siblings
        else "No metric over this measure leaves that field free; define one."
    )
    refusals = [
        Refusal(
            code="fixed_by_definition",
            message=(
                f"{declared.field!r} is fixed to {declared.value!r} by the definition of "
                f"{context.metric.name!r}, so it is not a cut of this metric."
            ),
            field=where,
            offending_value=requested,
            remediation=where_else,
            valid_alternatives=siblings,
        )
        for where, requested, declared in context.fixed_requests
    ]
    refusals += [
        Refusal(
            code="contradictory_filter",
            message=(
                f"{context.metric.name!r} is defined with {declared.field} {declared.operator} "
                f"{declared.value!r}, so this filter can never match — the answer would be an "
                f"empty result that reads like a real zero."
            ),
            field="filters",
            offending_value=requested,
            remediation=where_else,
            valid_alternatives=siblings,
        )
        for requested, declared in context.contradictions
    ]
    return refusals


def _dimensions(context: Context) -> list[Refusal]:
    refusals = [
        Refusal(
            code="unknown_dimension",
            message=f"{name!r} is not an authorised cut of {context.metric.name!r}.",
            field="dimensions",
            offending_value=name,
            remediation=(
                "Use a dimension from the metric signature, or pick a metric that carries it."
            ),
            valid_alternatives=close_matches(name, context.dimension_names),
        )
        for name in context.unknown_dimensions
    ]
    refusals += [
        Refusal(
            code="ambiguous_dimension",
            message=(
                f"{name!r} exists in more than one table reachable from {context.metric.name!r}."
            ),
            field="dimensions",
            offending_value=name,
            remediation="Name the cut by its entity, as entity__dimension.",
            valid_alternatives=alternatives,
        )
        for name, alternatives in context.ambiguous_dimensions
    ]
    return refusals


def _time_grain(context: Context) -> list[Refusal]:
    grain = context.request.time_grain
    if grain is None or grain in context.supported_grains:
        return []
    if context.rolls_up_undeclared:
        return []  # the snapshot rule explains this one, and one refusal beats two
    known = grain in GRAIN_ORDER
    reason = (
        f"{grain!r} is finer than {context.base_model.name!r}, which is stored at "
        f"{context.base_grain!r} grain"
        if known
        else f"{grain!r} is not a time grain this gateway serves"
    )
    return [
        Refusal(
            code="unsupported_time_grain",
            message=f"{reason}.",
            field="time_grain",
            offending_value=grain,
            remediation="Choose a grain from the metric signature.",
            valid_alternatives=context.supported_grains,
        )
    ]


def _date_range(context: Context) -> list[Refusal]:
    partition = context.partition.name if context.partition else "the partition column"
    if context.request.date_range is None:
        return [
            Refusal(
                code="missing_partition_filter",
                message=f"a date_range on {partition} is required: an unbounded scan is refused.",
                field="date_range",
                remediation="Supply date_range {start_date, end_date} as ISO dates.",
            )
        ]
    if context.unparseable_date is not None:
        return [
            Refusal(
                code="invalid_date_range",
                message=f"{context.unparseable_date!r} is not an ISO date.",
                field="date_range",
                offending_value=context.unparseable_date,
                remediation="Use YYYY-MM-DD for start_date and end_date.",
            )
        ]
    if context.reversed_dates:
        return [
            Refusal(
                code="invalid_date_range",
                message="date_range.end_date precedes start_date.",
                field="date_range",
                offending_value=context.request.date_range.end_date,
                remediation="Supply an inclusive range where start_date <= end_date.",
            )
        ]
    return []


def _window_cap(context: Context) -> list[Refusal]:
    cap = context.metric.max_window_days
    if cap is None or context.date_range is None:
        return []
    days = (context.date_range.end_date - context.date_range.start_date).days
    if days <= cap:
        return []
    return [
        Refusal(
            code="partition_window_too_wide",
            message=f"requested {days} days; {context.metric.name!r} allows {cap} per query.",
            field="date_range",
            offending_value=days,
            remediation=f"Narrow the range to {cap} days or fewer and page through the window.",
        )
    ]


def _snapshot_rollup(context: Context) -> list[Refusal]:
    """The expensive silent error: summing a snapshot across time counts the same stock twice."""
    if not context.rolls_up_undeclared:
        return []
    grain = context.request.time_grain
    if grain == context.base_grain:
        return []
    return [
        Refusal(
            code="non_additive_cut",
            message=(
                f"{context.metric.name!r} is a snapshot with no declared roll-up: summing it "
                "across time would count the same stock once per period."
            ),
            field="time_grain",
            offending_value=grain,
            remediation=(
                f"Query at {context.base_grain!r} grain, or declare non_additive_dimension on "
                "the measure so it can be rolled up."
            ),
            valid_alternatives=[context.base_grain],
        )
    ]


def _filters(context: Context) -> list[Refusal]:
    refusals = [
        Refusal(
            code="unsupported_operator",
            message=f"{operator!r} is not a supported filter operator.",
            field="filters",
            offending_value=operator,
            remediation="Use one of the operators in the metric signature.",
            valid_alternatives=list(OPERATORS),
        )
        for operator in context.bad_operators
    ]
    refusals += [
        Refusal(
            code="unknown_filter_field",
            message=f"cannot filter {context.metric.name!r} on {name!r}.",
            field="filters",
            offending_value=name,
            remediation="Filter on a dimension or the metric itself, as named in the signature.",
            valid_alternatives=close_matches(name, context.filter_fields),
        )
        for name in context.unknown_filter_fields
    ]
    return refusals


def _order_by(context: Context) -> list[Refusal]:
    allowed = context.order_fields
    return [
        Refusal(
            code="unknown_order_field",
            message=f"cannot order by {clause.field!r}: it is not in the result set.",
            field="order_by",
            offending_value=clause.field,
            remediation="Order by a requested dimension, 'period', or the metric itself.",
            valid_alternatives=allowed,
        )
        for clause in context.request.order_by
        if clause.field not in allowed
    ]


def _row_limit(context: Context) -> list[Refusal]:
    if context.request.row_limit <= MAX_ROW_LIMIT:
        return []
    return [
        Refusal(
            code="row_limit_exceeded",
            message=(
                f"row_limit {context.request.row_limit} exceeds the ceiling of {MAX_ROW_LIMIT}."
            ),
            field="row_limit",
            offending_value=context.request.row_limit,
            remediation=(
                f"Request at most {MAX_ROW_LIMIT} rows; aggregate further if you need a "
                "broader view."
            ),
        )
    ]


RULES: tuple[Rule, ...] = (
    Rule("metric_shape", ("unsupported_metric_shape",), ("type",), _metric_shape),
    Rule("dimensions", ("unknown_dimension", "ambiguous_dimension"), ("dimensions",), _dimensions),
    Rule(
        "definition_constraints",
        ("fixed_by_definition", "contradictory_filter"),
        ("dimensions", "filters"),
        _definition_constraints,
    ),
    Rule("time_grain", ("unsupported_time_grain",), ("time_grains",), _time_grain),
    Rule(
        "date_range",
        ("missing_partition_filter", "invalid_date_range"),
        ("required_filters",),
        _date_range,
    ),
    Rule("window_cap", ("partition_window_too_wide",), ("required_filters",), _window_cap),
    Rule(
        "snapshot_rollup",
        ("non_additive_cut",),
        ("additive", "non_additive_dimension", "time_grains"),
        _snapshot_rollup,
    ),
    Rule("filters", ("unknown_filter_field", "unsupported_operator"), ("filters",), _filters),
    Rule("order_by", ("unknown_order_field",), ("order_by",), _order_by),
    Rule("row_limit", ("row_limit_exceeded",), ("row_limit",), _row_limit),
)

# The public error vocabulary. Codes are added, never silently repurposed: agents branch on them.
CODES: frozenset[str] = frozenset(
    {"unknown_metric", *(code for rule in RULES for code in rule.codes)}
)
