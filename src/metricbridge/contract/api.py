"""`validate` and `signature` — the two halves of one promise.

Both are built from the same rule registry, so a constraint cannot be enforced without being
advertised. Everything here happens before SQL exists: a refusal names the metric's own vocabulary
("region is not a cut of inventory_on_hand"), never the database's ("column not found").
"""

from ..discovery import rank_names
from ..manifest import OPERATORS, SemanticManifest
from .context import Resolved, build_context
from .errors import Refusal, RefusalError
from .request import DEFAULT_ROW_LIMIT, MAX_ROW_LIMIT, QueryRequest
from .rules import RULES


def unknown_metric(manifest: SemanticManifest, name: str) -> Refusal:
    """The one refusal raised before any rule can run: the metric is not in the manifest."""
    return Refusal(
        code="unknown_metric",
        message=f"no governed metric named {name!r}.",
        field="metric",
        offending_value=name,
        remediation="Call discover_metrics to find the certified name for this concept.",
        valid_alternatives=rank_names(name, sorted(manifest.metrics), limit=5),
    )


def validate(manifest: SemanticManifest, request: QueryRequest) -> Resolved:
    """Return the resolved request, or raise `RefusalError` carrying every problem found."""
    metric = manifest.metrics.get(request.metric)
    if metric is None:
        raise RefusalError([unknown_metric(manifest, request.metric)])

    context = build_context(manifest, request, metric)
    refusals = [refusal for rule in RULES for refusal in rule.check(context)]
    if refusals:
        raise RefusalError(refusals)

    assert context.date_range is not None and context.partition is not None
    return Resolved(
        metric=metric,
        base_model=context.base_model,
        partition=context.partition,
        dimensions=context.dimensions,
        where_filters=context.where_filters,
        having_filters=context.having_filters,
        date_range=context.date_range,
        time_grain=request.time_grain,
        row_limit=request.row_limit,
        snapshot=context.snapshot,
        metric_filters=context.metric_filters,
        notices=context.notices,
    )


def signature(manifest: SemanticManifest, metric_name: str) -> dict:
    """The exhaustive contract for one metric. After reading this, an agent guesses at nothing."""
    metric = manifest.metrics[metric_name]
    context = build_context(manifest, QueryRequest(metric=metric_name), metric)
    partition = context.partition

    return {
        "metric": metric.name,
        "type": metric.type,
        "description": metric.description,
        "tier": metric.tier,
        "owner": metric.owner,
        "replaced_by": metric.replaced_by,
        "synonyms": metric.synonyms,
        "table": context.base_model.table,
        "window": f"{metric.window.count} {metric.window.granularity}s" if metric.window else None,
        "grain_to_date": metric.grain_to_date,
        "additive": context.additive,
        "non_additive_dimension": context.snapshot.model_dump() if context.snapshot else None,
        "time_grains": context.supported_grains,
        "time_grain_required": context.rolls_up_undeclared,
        "dimensions": [
            {
                "name": dimension.name,
                "kind": dimension.kind,
                "model": dimension.model,
                "entity": dimension.entity,
            }
            for dimension in (context.catalog[name] for name in context.dimension_names)
        ],
        "required_filters": [
            {
                "field": partition.name if partition else None,
                "reason": "partition pruning is mandatory; an unbounded scan is refused",
                "shape": "date_range {start_date, end_date}",
                "max_window_days": metric.max_window_days,
            }
        ],
        "filters": {
            "fields": context.filter_fields,
            "operators": list(OPERATORS),
            "always_applied": [
                {"field": f.field, "operator": f.operator, "value": f.value} for f in metric.filters
            ],
        },
        "order_by": {
            "fields": "any requested dimension, 'period', or the metric itself",
            "directions": ["asc", "desc"],
        },
        "row_limit": {"default": DEFAULT_ROW_LIMIT, "maximum": MAX_ROW_LIMIT},
        "notices": context.notices,
    }
