"""Request validation against the metric's declared contract.

Every refusal here happens *before* SQL exists, which is the point: the agent is corrected in
the vocabulary of the semantic layer ("region is not a cut of inventory_on_hand") rather than
in the vocabulary of the database ("column not found"), and the second one teaches it nothing.

**All errors are collected, never just the first.** An agent that repairs one mistake per
round trip burns its context on ceremony.
"""

from __future__ import annotations

import difflib
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from .errors import SemanticError, SemanticRefusal
from .manifest import GRAIN_ORDER, Metric, SemanticManifest, TimeGrain

OPERATORS = ("=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "IS NULL", "IS NOT NULL")
MAX_ROW_LIMIT = 1000


class DateRange(BaseModel):
    start_date: date
    end_date: date


class Filter(BaseModel):
    field: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "IS NULL", "IS NOT NULL"]
    value: Any = None


class OrderBy(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class QueryRequest(BaseModel):
    metric_name: str
    date_range: DateRange
    dimensions: list[str] = Field(default_factory=list)
    time_grain: TimeGrain | None = None
    filters: list[Filter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    row_limit: int = 100


def signature(metric: Metric) -> dict:
    """The authoritative structural contract for one metric — what `get_metric_signature`
    returns. Deliberately exhaustive: an agent that has this never needs to guess."""
    return {
        "metric_name": metric.name,
        "description": metric.description,
        "domain": metric.domain,
        "tier": metric.tier,
        "owner": metric.owner,
        "entity_grain": metric.table,
        "expression": metric.expression,
        "additive": metric.additive,
        "time_dimension": metric.time_dimension,
        "supported_time_grains": metric.time_grains,
        "dimensions": [
            {"name": d.name, "kind": d.kind, "description": d.description}
            for d in metric.dimensions
        ],
        "non_additive_dimensions": metric.non_additive_dimensions,
        "required_filters": [
            {
                "field": metric.time_dimension,
                "reason": "partition pruning is mandatory; an unbounded scan is refused",
                "shape": "date_range {start_date, end_date}",
                "max_window_days": metric.max_window_days,
            }
        ],
        "row_limit": {"default": 100, "maximum": MAX_ROW_LIMIT},
    }


def _closest(value: str, options: list[str]) -> list[str]:
    return difflib.get_close_matches(value, options, n=3, cutoff=0.4) or options[:3]


def validate(manifest: SemanticManifest, request: QueryRequest) -> Metric:
    """Return the metric, or raise SemanticRefusal carrying every problem found."""
    metric = manifest.get(request.metric_name)
    if metric is None:
        raise SemanticRefusal([SemanticError(
            code="unknown_metric",
            message=f"No governed metric named {request.metric_name!r}.",
            field="metric_name",
            offending_value=request.metric_name,
            remediation="Call discover_metrics to find the certified name for this concept.",
            valid_alternatives=_closest(request.metric_name, sorted(manifest.metrics)))])

    errors: list[SemanticError] = []
    known = metric.dimension_names

    for dimension in request.dimensions:
        if dimension not in known:
            errors.append(SemanticError(
                code="unknown_dimension",
                message=f"{dimension!r} is not an authorized cut of {metric.name!r}.",
                field="dimensions", offending_value=dimension,
                remediation="Use a dimension from the metric signature, or pick a metric that "
                            "carries this cut.",
                valid_alternatives=_closest(dimension, known)))
        elif dimension in metric.non_additive_dimensions:
            # The expensive silent error: a plausible number that double-counts.
            errors.append(SemanticError(
                code="non_additive_cut",
                message=f"{metric.name!r} cannot be aggregated across {dimension!r} — the "
                        f"measure is not additive along it, so the total would double-count.",
                field="dimensions", offending_value=dimension,
                remediation="Cut by an additive dimension, or query the metric at its base "
                            "grain and inspect rows rather than a total.",
                valid_alternatives=[d for d in known if d not in metric.non_additive_dimensions][:3]))

    if request.time_grain and request.time_grain not in metric.time_grains:
        errors.append(SemanticError(
            code="unsupported_time_grain",
            message=f"{metric.name!r} is not defined at {request.time_grain!r} grain.",
            field="time_grain", offending_value=request.time_grain,
            remediation="Choose a supported grain from the metric signature.",
            valid_alternatives=list(metric.time_grains)))

    if not metric.additive and request.time_grain and request.time_grain != "day":
        errors.append(SemanticError(
            code="non_additive_cut",
            message=f"{metric.name!r} is a snapshot measure: summing it across time "
                    f"double-counts every period.",
            field="time_grain", offending_value=request.time_grain,
            remediation="Query at 'day' grain, or use a metric defined as a period change.",
            valid_alternatives=["day"]))

    window = (request.date_range.end_date - request.date_range.start_date).days
    if window < 0:
        errors.append(SemanticError(
            code="missing_partition_filter",
            message="date_range.end_date precedes start_date.",
            field="date_range", offending_value=str(request.date_range.start_date),
            remediation="Supply an inclusive range where start_date <= end_date."))
    elif metric.max_window_days and window > metric.max_window_days:
        errors.append(SemanticError(
            code="partition_window_too_wide",
            message=f"Requested {window} days; {metric.name!r} allows "
                    f"{metric.max_window_days} per query.",
            field="date_range", offending_value=window,
            remediation=f"Narrow the range to {metric.max_window_days} days or fewer and "
                        f"page through the window."))

    orderable = {*request.dimensions, metric.name, "period"}
    for clause in request.order_by:
        if clause.field not in orderable:
            errors.append(SemanticError(
                code="unknown_dimension",
                message=f"Cannot order by {clause.field!r}: it is not in the result set.",
                field="order_by", offending_value=clause.field,
                remediation="Order by a requested dimension, 'period', or the metric itself.",
                valid_alternatives=sorted(orderable)))

    for filter_ in request.filters:
        if filter_.field not in known:
            errors.append(SemanticError(
                code="unknown_filter_field",
                message=f"Cannot filter {metric.name!r} on {filter_.field!r}.",
                field="filters", offending_value=filter_.field,
                remediation="Filter on a dimension named in the metric signature.",
                valid_alternatives=_closest(filter_.field, known)))

    if request.row_limit > MAX_ROW_LIMIT:
        errors.append(SemanticError(
            code="row_limit_exceeded",
            message=f"row_limit {request.row_limit} exceeds the ceiling of {MAX_ROW_LIMIT}.",
            field="row_limit", offending_value=request.row_limit,
            remediation=f"Request at most {MAX_ROW_LIMIT} rows; aggregate further if you need "
                        f"a broader view."))

    if errors:
        raise SemanticRefusal(errors)
    return metric


def coarser_than(grain: TimeGrain, base: TimeGrain) -> bool:
    return GRAIN_ORDER.index(grain) > GRAIN_ORDER.index(base)
