"""One resolution pass over a request, so the rules judge the same reading the compiler will use.

Dimension names follow MetricFlow: a cut of the metric's own table is named plainly, a cut reached
through a join is `entity__dimension`. A bare name is accepted while it points at exactly one
thing; when it does not, the ambiguity is refused with the qualified names offered.
"""

import difflib
from dataclasses import dataclass, field
from datetime import date

from ..manifest import (
    OPERATORS,
    CatalogEntry,
    Dimension,
    Measure,
    Metric,
    MetricFilter,
    NonAdditiveDimension,
    SemanticManifest,
    SemanticModel,
    dimension_catalog,
    metric_sources,
)
from ..manifest import resolve as resolve_in_catalog
from .request import QueryRequest

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
    order_by: list[tuple[str, str]]  # (field, direction), already checked against the result set
    row_limit: int
    snapshot: NonAdditiveDimension | None
    metric_filters: list[ResolvedFilter]  # declared on the metric, applied to every query
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
    catalog: dict[str, CatalogEntry]  # qualified name -> reachable cut
    dimensions: list[ResolvedDimension] = field(default_factory=list)
    unknown_dimensions: list[str] = field(default_factory=list)
    ambiguous_dimensions: list[tuple[str, list[str]]] = field(default_factory=list)
    where_filters: list[ResolvedFilter] = field(default_factory=list)
    having_filters: list[ResolvedFilter] = field(default_factory=list)
    metric_filters: list[ResolvedFilter] = field(default_factory=list)
    pinned: dict[str, MetricFilter] = field(default_factory=dict)
    constrained: dict[str, MetricFilter] = field(default_factory=dict)
    fixed_requests: list[tuple[str, str, MetricFilter]] = field(default_factory=list)
    contradictions: list[tuple[str, MetricFilter]] = field(default_factory=list)
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
    def unfiltered_siblings(self) -> list[str]:
        """Metrics over the same measures that leave every field free — where to send an agent
        whose question the definition of this metric excludes."""
        mine = {m.name for m in self.measures}
        return sorted(
            name
            for name, other in self.manifest.metrics.items()
            if name != self.metric.name
            and not other.filters
            and other.tier != "deprecated"
            and other.measure in mine
        )

    @property
    def dimension_names(self) -> list[str]:
        """Ordered as the model reads: this table's own cuts, then each join's, by entity.

        A field the definition pins to one value is not a cut of this metric at all: grouping by it
        yields one row and filtering it can only be redundant or empty. Advertising it would be the
        signature contradicting the definition.

        A flat alphabetical list buries the cut the agent most likely wants among cuts it has to
        traverse a join to reach.
        """
        free = {name: d for name, d in self.catalog.items() if name not in self.pinned}
        own = sorted(name for name, d in free.items() if d.entity is None)
        joined = sorted((d.entity, name) for name, d in free.items() if d.entity is not None)
        return own + [name for _, name in joined]

    @property
    def descriptions(self) -> dict[str, str]:
        return {name: entry.description for name, entry in self.catalog.items()}

    @property
    def filter_fields(self) -> list[str]:
        return [self.metric.name, *self.dimension_names]

    @property
    def order_fields(self) -> list[str]:
        fields = {d.requested for d in self.dimensions} | {self.metric.name}
        if self.request.time_grain:
            fields.add("period")
        return sorted(fields)


def _as_dimension(entry: CatalogEntry, requested: str) -> ResolvedDimension:
    return ResolvedDimension(
        requested=requested,
        name=entry.name,
        model=entry.model,
        column=entry.column,
        entity=entry.entity,
        kind=entry.kind,
    )


def _resolve_name(context: "Context", requested: str) -> ResolvedDimension | list[str] | None:
    """The dimension, or the qualified alternatives when a bare name is ambiguous, or None."""
    outcome = resolve_in_catalog(context.catalog, requested)
    if outcome is None or isinstance(outcome, list):
        return outcome
    return _as_dimension(outcome, requested)


def build_context(manifest: SemanticManifest, request: QueryRequest, metric: Metric) -> Context:
    measures, models = metric_sources(manifest.semantic_models, manifest.metrics, metric)
    base = models[0]
    context = Context(
        manifest=manifest,
        request=request,
        metric=metric,
        base_model=base,
        measures=measures,
        partition=base.partition,
        catalog=dimension_catalog(manifest.semantic_models, manifest.joins, base),
        mixed_models=len({m.name for m in models}) > 1,
    )

    for declared in metric.filters:  # part of the definition; validated when the manifest loaded
        entry = resolve_in_catalog(context.catalog, declared.field)
        if isinstance(entry, CatalogEntry):
            target = context.pinned if declared.operator == "=" else context.constrained
            target[entry.name] = declared

        outcome = resolve_in_catalog(context.catalog, declared.field)
        if isinstance(outcome, CatalogEntry):
            context.metric_filters.append(
                ResolvedFilter(
                    declared.field,
                    declared.operator,
                    declared.value,
                    _as_dimension(outcome, declared.field),
                )
            )

    for requested in request.dimensions:
        outcome = _resolve_name(context, requested)
        if outcome is None:
            context.unknown_dimensions.append(requested)
        elif isinstance(outcome, list):
            context.ambiguous_dimensions.append((requested, outcome))
        elif outcome.name in context.pinned:
            context.fixed_requests.append(("dimensions", requested, context.pinned[outcome.name]))
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
        elif outcome.name in context.pinned:
            context.fixed_requests.append(("filters", filter_.field, context.pinned[outcome.name]))
        elif outcome.name in context.constrained and disjoint(
            context.constrained[outcome.name], filter_.operator.lower(), filter_.value
        ):
            context.contradictions.append((filter_.field, context.constrained[outcome.name]))
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


def _as_set(value: object) -> set:
    return set(value) if isinstance(value, list) else {value}


def disjoint(declared: MetricFilter, operator: str, value: object) -> bool:
    """Can these two filters never both hold? Decided only for literal set operators.

    `LIKE` and range comparisons are left alone on purpose: a refusal resting on a shaky proof is
    its own failure mode.
    """
    literal = ("=", "!=", "in", "not in")
    if declared.operator not in literal or operator not in literal:
        return False
    allowed, requested = _as_set(declared.value), _as_set(value)
    declared_positive = declared.operator in ("=", "in")
    requested_positive = operator in ("=", "in")
    if declared_positive and requested_positive:
        return not (allowed & requested)
    if declared_positive and not requested_positive:
        return allowed <= requested  # the request excludes everything the definition allows
    if not declared_positive and requested_positive:
        return requested <= allowed  # the definition excludes everything the request asks for
    return False  # two exclusions always share something


def close_matches(value: str, options: list[str], limit: int = 25) -> list[str]:
    """Near names for a typo; otherwise what is actually on offer.

    A weak match costs the agent a turn: `colour` is not a misspelling of `channel`, and offering
    it as one sends the next request somewhere just as wrong.
    """
    strong = difflib.get_close_matches(value, options, n=3, cutoff=0.6)
    if strong:
        return strong
    return list(options)[:limit]
