"""One resolution pass over a request, so the rules judge the same reading the compiler will use.

Dimension names follow MetricFlow: a cut of the metric's own table is named plainly, a cut reached
through a join is `entity__dimension`. A bare name is accepted while it points at exactly one
thing; when it does not, the ambiguity is refused with the qualified names offered.
"""

import difflib
from dataclasses import dataclass, field
from datetime import date

from ..manifest import (
    Dimension,
    Measure,
    Metric,
    NonAdditiveDimension,
    SemanticManifest,
    SemanticModel,
)
from .request import OPERATORS, QueryRequest

GRAIN_ORDER = ("day", "week", "month", "quarter", "year")


@dataclass(frozen=True)
class ResolvedDimension:
    requested: str
    name: str  # the qualified name, which is how the signature advertises it
    model: str
    column: str
    entity: str | None
    kind: str


@dataclass(frozen=True)
class ResolvedFilter:
    field: str
    operator: str
    value: object
    dimension: ResolvedDimension | None  # None means it filters the metric, so it lands in HAVING


@dataclass(frozen=True)
class DateRange:
    start_date: date
    end_date: date


@dataclass(frozen=True)
class Resolved:
    """A request that passed every rule, in the terms the compiler needs."""

    metric: Metric
    base_model: SemanticModel
    partition: Dimension
    dimensions: list[ResolvedDimension]
    where_filters: list[ResolvedFilter]
    having_filters: list[ResolvedFilter]
    date_range: DateRange
    time_grain: str | None
    row_limit: int
    snapshot: NonAdditiveDimension | None
    notices: list[str]


@dataclass
class Context:
    """Everything a rule needs, resolved once."""

    manifest: SemanticManifest
    request: QueryRequest
    metric: Metric
    base_model: SemanticModel
    measures: tuple[Measure, ...]
    partition: Dimension | None
    catalog: dict[str, ResolvedDimension]  # qualified name -> dimension
    dimensions: list[ResolvedDimension] = field(default_factory=list)
    unknown_dimensions: list[str] = field(default_factory=list)
    ambiguous_dimensions: list[tuple[str, list[str]]] = field(default_factory=list)
    where_filters: list[ResolvedFilter] = field(default_factory=list)
    having_filters: list[ResolvedFilter] = field(default_factory=list)
    unknown_filter_fields: list[str] = field(default_factory=list)
    bad_operators: list[str] = field(default_factory=list)
    date_range: DateRange | None = None
    unparseable_date: str | None = None
    reversed_dates: bool = False
    mixed_models: bool = False
    notices: list[str] = field(default_factory=list)

    @property
    def additive(self) -> bool:
        return all(m.additive for m in self.measures)

    @property
    def snapshot(self) -> NonAdditiveDimension | None:
        declared = [m.non_additive_dimension for m in self.measures if m.non_additive_dimension]
        return declared[0] if declared else None

    @property
    def rolls_up_undeclared(self) -> bool:
        return any(not m.additive and m.non_additive_dimension is None for m in self.measures)

    @property
    def base_grain(self) -> str:
        return self.partition.time_granularity if self.partition else "day"

    @property
    def supported_grains(self) -> list[str]:
        if self.rolls_up_undeclared:
            return [self.base_grain]
        return list(GRAIN_ORDER[GRAIN_ORDER.index(self.base_grain) :])

    @property
    def dimension_names(self) -> list[str]:
        """Ordered as the model reads: this table's own cuts, then each join's, by entity.

        A flat alphabetical list buries the cut the agent most likely wants among cuts it has to
        traverse a join to reach.
        """
        own = sorted(name for name, d in self.catalog.items() if d.entity is None)
        joined = sorted(
            (d.entity, name) for name, d in self.catalog.items() if d.entity is not None
        )
        return own + [name for _, name in joined]

    @property
    def filter_fields(self) -> list[str]:
        return [self.metric.name, *self.dimension_names]

    @property
    def order_fields(self) -> list[str]:
        fields = {d.requested for d in self.dimensions} | {self.metric.name}
        if self.request.time_grain:
            fields.add("period")
        return sorted(fields)


def measures_of(
    manifest: SemanticManifest, metric: Metric
) -> tuple[tuple[Measure, ...], tuple[SemanticModel, ...]]:
    """The measures a metric reads, and the semantic models that own them."""
    names: list[str] = []
    if metric.type == "ratio":
        for leg in (metric.numerator, metric.denominator):
            names.append(manifest.metrics[leg].measure)
    else:
        names.append(metric.measure)
    measures: list[Measure] = []
    models: list[SemanticModel] = []
    for name in names:
        for model in manifest.semantic_models.values():
            found = next((m for m in model.measures if m.name == name), None)
            if found is not None:
                measures.append(found)
                models.append(model)
                break
    return tuple(measures), tuple(models)


def _catalog(manifest: SemanticManifest, base: SemanticModel) -> dict[str, ResolvedDimension]:
    catalog: dict[str, ResolvedDimension] = {}
    for dimension in base.dimensions:
        catalog[dimension.name] = ResolvedDimension(
            requested=dimension.name,
            name=dimension.name,
            model=base.name,
            column=dimension.expr,
            entity=None,
            kind=dimension.type,
        )
    for join in manifest.joins:
        if join.from_model != base.name:
            continue
        target = manifest.semantic_models[join.to_model]
        for dimension in target.dimensions:
            qualified = f"{join.entity}__{dimension.name}"
            catalog[qualified] = ResolvedDimension(
                requested=qualified,
                name=qualified,
                model=target.name,
                column=dimension.expr,
                entity=join.entity,
                kind=dimension.type,
            )
    return catalog


def _resolve_name(context: Context, requested: str) -> ResolvedDimension | list[str] | None:
    """The dimension, or the qualified alternatives when a bare name is ambiguous, or None."""
    found = context.catalog.get(requested)
    if found is not None:
        return found
    candidates = [d for name, d in context.catalog.items() if name.endswith(f"__{requested}")]
    if not candidates:
        return None
    if len(candidates) > 1:
        return sorted(d.name for d in candidates)
    resolved = candidates[0]
    return ResolvedDimension(
        requested=requested,
        name=resolved.name,
        model=resolved.model,
        column=resolved.column,
        entity=resolved.entity,
        kind=resolved.kind,
    )


def build_context(manifest: SemanticManifest, request: QueryRequest, metric: Metric) -> Context:
    measures, models = measures_of(manifest, metric)
    base = models[0]
    context = Context(
        manifest=manifest,
        request=request,
        metric=metric,
        base_model=base,
        measures=measures,
        partition=base.partition,
        catalog=_catalog(manifest, base),
        mixed_models=len({m.name for m in models}) > 1,
    )

    for requested in request.dimensions:
        outcome = _resolve_name(context, requested)
        if outcome is None:
            context.unknown_dimensions.append(requested)
        elif isinstance(outcome, list):
            context.ambiguous_dimensions.append((requested, outcome))
        else:
            context.dimensions.append(outcome)

    for filter_ in request.filters:
        if filter_.operator.lower() not in OPERATORS:
            context.bad_operators.append(filter_.operator)
            continue
        if filter_.field == metric.name:
            context.having_filters.append(
                ResolvedFilter(filter_.field, filter_.operator.lower(), filter_.value, None)
            )
            continue
        outcome = _resolve_name(context, filter_.field)
        if outcome is None or isinstance(outcome, list):
            context.unknown_filter_fields.append(filter_.field)
        else:
            context.where_filters.append(
                ResolvedFilter(filter_.field, filter_.operator.lower(), filter_.value, outcome)
            )

    if request.date_range is not None:
        try:
            start = date.fromisoformat(request.date_range.start_date)
            end = date.fromisoformat(request.date_range.end_date)
        except ValueError:
            for value in (request.date_range.start_date, request.date_range.end_date):
                try:
                    date.fromisoformat(value)
                except ValueError:
                    context.unparseable_date = value
                    break
        else:
            context.reversed_dates = end < start
            context.date_range = DateRange(start, end)

    if metric.tier == "deprecated":
        successor = f"; use {metric.replaced_by!r} instead" if metric.replaced_by else ""
        context.notices.append(f"metric {metric.name!r} is deprecated{successor}")
    return context


def close_matches(value: str, options: list[str], limit: int = 25) -> list[str]:
    """Near names for a typo; otherwise what is actually on offer.

    A weak match costs the agent a turn: `colour` is not a misspelling of `channel`, and offering
    it as one sends the next request somewhere just as wrong.
    """
    strong = difflib.get_close_matches(value, options, n=3, cutoff=0.6)
    if strong:
        return strong
    return list(options)[:limit]
