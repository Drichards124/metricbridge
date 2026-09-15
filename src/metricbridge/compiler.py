"""Compile a validated request into one parameterised statement.

The agent never writes SQL and never sees a database: it sends a structured request and this builds
the statement. That inversion is the firewall — there is no prompt-injection path into query text,
because query text is not agent-supplied. Values travel as bound parameters, so a filter value like
`O'Brien'; DROP TABLE` reaches the database as a string and nothing else.

Three shapes, one scan. A simple metric aggregates the scan; a ratio aggregates it twice and divides
after grouping; a snapshot ranks it and keeps one row per group before aggregating. Sharing the scan
is what stops their bounds, joins and filters from drifting apart.

Everything is built from typed sqlglot expressions rather than assembled strings, which is why a
time bucket comes out as `DATE_TRUNC(col, MONTH)` on BigQuery and `dateTrunc(...)` on ClickHouse
instead of one spelling that is wrong on four engines.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlglot import exp, parse_one

from .contract import Resolved, ResolvedFilter
from .manifest import (
    Measure,
    Metric,
    SemanticManifest,
    SemanticModel,
    dimension_catalog,
    metric_sources,
    resolve,
)

DIALECTS = ("duckdb", "postgres", "bigquery", "snowflake", "clickhouse")

_AGGREGATES = {
    "sum": exp.Sum,
    "count": exp.Count,
    "min": exp.Min,
    "max": exp.Max,
    "average": exp.Avg,
}
_COMPARISONS = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}
# Totals per joined entity, carried on every row: accounting for the answer, not part of it.
UNRECONCILED_SUFFIXES = ("__unreconciled_rows", "__empty_key_rows", "__unreconciled_value")


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: dict[str, object]
    dialect: str
    columns: list[str]
    output_window: tuple[date, date]  # what the answer covers, half-open
    scan_window: tuple[date, date]  # what the scan reads, widened by any trailing window
    expected_periods: list[date] = field(default_factory=list)
    """Every anchor the range should produce. A period with no base rows never becomes an anchor,
    so execution can report the missing ones rather than leaving a silent hole (see
    docs/failure-modes.md, anchor dropping)."""


@dataclass
class _Scan:
    """The rows a metric reads: one table, its joins, its bounds and its filters."""

    base: SemanticModel
    alias: str
    business: exp.Column
    conditions: list[exp.Expression]
    joins: dict[str, str] = field(default_factory=dict)  # entity -> semantic model reached


_MONTHS = {"month": 1, "quarter": 3, "year": 12}


def _shift(moment: date, count: int, granularity: str) -> date:
    """Move a date by whole periods. Calendar months are not 30 days, and a trailing-twelve-month
    window that drifts is a wrong number nobody notices."""
    if granularity == "day":
        return moment + timedelta(days=count)
    if granularity == "week":
        return moment + timedelta(weeks=count)
    months = count * _MONTHS[granularity]
    total = moment.month - 1 + months
    year, month = moment.year + total // 12, total % 12 + 1
    return date(year, month, min(moment.day, monthrange(year, month)[1]))


def _truncate(moment: date, granularity: str) -> date:
    if granularity == "day":
        return moment
    if granularity == "week":
        return moment - timedelta(days=moment.weekday())
    if granularity == "month":
        return moment.replace(day=1)
    if granularity == "quarter":
        return moment.replace(month=(moment.month - 1) // 3 * 3 + 1, day=1)
    return moment.replace(month=1, day=1)


def _periods_in(start: date, end_exclusive: date, granularity: str) -> list[date]:
    periods, moment = [], _truncate(start, granularity)
    while moment < end_exclusive:
        periods.append(moment)
        moment = _shift(moment, 1, granularity)
    return periods


def _qualify(expression: exp.Expression, table: str) -> exp.Expression:
    """Point every bare column at the table it came from, so joins cannot make it ambiguous."""
    for column in expression.find_all(exp.Column):
        if not column.table:
            column.set("table", exp.to_identifier(table))
    return expression


def _aggregate(measure: Measure, alias: str, dialect: str) -> exp.Expression:
    """The measure, recomputed from base rows.

    Never from a finer aggregate: summing daily distinct counts into a month counts a returning
    customer once per day, and an average of averages is not the period average.
    """
    inner = _qualify(parse_one(measure.expr, dialect=dialect), alias)
    if measure.agg == "count_distinct":
        return exp.Count(this=exp.Distinct(expressions=[inner]))
    return _AGGREGATES[measure.agg](this=inner)


def _placeholder(name: str) -> exp.Placeholder:
    return exp.Placeholder(this=name)


def _predicate(
    column: exp.Expression, filter_: ResolvedFilter, name: str, parameters: dict[str, object]
) -> exp.Expression:
    operator = filter_.operator
    if operator == "is null":
        return exp.Is(this=column, expression=exp.Null())
    if operator == "is not null":
        return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
    if operator in ("in", "not in"):
        values = filter_.value if isinstance(filter_.value, list) else [filter_.value]
        placeholders = []
        for index, value in enumerate(values):
            parameters[f"{name}_{index}"] = value
            placeholders.append(_placeholder(f"{name}_{index}"))
        membership = exp.In(this=column, expressions=placeholders)
        return exp.Not(this=membership) if operator == "not in" else membership
    parameters[name] = filter_.value
    if operator == "like":
        return exp.Like(this=column, expression=_placeholder(name))
    return _COMPARISONS[operator](this=column, expression=_placeholder(name))


def _column_of(resolved: Resolved, dimension) -> exp.Column:
    return exp.column(dimension.column, table=dimension.entity or resolved.base_model.name)


def _join(base: SemanticModel, target: SemanticModel, entity: str) -> exp.Join:
    """Always LEFT: an inner join silently drops null-key rows, shrinking the denominator."""
    foreign = next(e for e in base.entities if e.name == entity)
    unique = next(e for e in target.entities if e.name == entity)
    condition = exp.EQ(
        this=exp.column(foreign.expr, table=base.name),
        expression=exp.column(unique.expr, table=entity),
    )
    return exp.Join(this=exp.to_table(target.table).as_(entity), on=condition, side="LEFT")


def _null_key_case(base: SemanticModel, entity: str, alias: str) -> exp.Expression:
    foreign = next(e for e in base.entities if e.name == entity)
    return exp.Case(
        ifs=[
            exp.If(
                this=exp.Is(this=exp.column(foreign.expr, table=alias), expression=exp.Null()),
                true=exp.Literal.number(1),
            )
        ],
        default=exp.Literal.number(0),
    )


def _unmatched(manifest: SemanticManifest, scan: _Scan, entity: str) -> exp.Expression:
    """No joined row matched: tested on the joined table's key, not the fact's. A key naming a
    customer who does not exist is not empty, but it matches nothing all the same."""
    target = manifest.semantic_models[scan.joins[entity]]
    key = next(e for e in target.entities if e.name == entity)
    return exp.Is(this=exp.column(key.expr, table=entity), expression=exp.Null())


def _totals(
    manifest: SemanticManifest, scan: _Scan, measure: Measure, dialect: str, *, label: str = ""
) -> tuple[exp.Select, list[str]]:
    """Over the whole scan, before grouping and the limit: per joined entity, the rows no joined row
    matched, how many of those had an empty key, and the measure over just those rows.
    """
    projections: list[exp.Expression] = []
    columns: list[str] = []
    for entity in sorted(scan.joins):
        unmatched = _unmatched(manifest, scan, entity)
        rows = exp.Case(ifs=[exp.If(this=unmatched, true=exp.Literal.number(1))])
        rows.set("default", exp.Literal.number(0))
        value = exp.Case(
            ifs=[
                exp.If(
                    this=unmatched.copy(),
                    true=_qualify(parse_one(measure.expr, dialect=dialect), scan.alias),
                )
            ]
        )
        if measure.agg == "count_distinct":
            aggregated = exp.Count(this=exp.Distinct(expressions=[value]))
        else:
            aggregated = _AGGREGATES[measure.agg](this=value)
        projections += [
            exp.alias_(exp.Sum(this=rows), f"{entity}{label}__unreconciled_rows"),
            exp.alias_(
                exp.Sum(this=_null_key_case(scan.base, entity, scan.alias)),
                f"{entity}{label}__empty_key_rows",
            ),
            exp.alias_(aggregated, f"{entity}{label}__unreconciled_value"),
        ]
        columns += [f"{entity}{label}{suffix}" for suffix in UNRECONCILED_SUFFIXES]
    return _apply(exp.select(*projections), manifest, scan), columns


def _with_totals(
    query: exp.Select, columns: list[str], totals: exp.Select, total_columns: list[str]
) -> tuple[exp.Select, list[str]]:
    """Carry the totals on every row of the answer. The answer's own CTEs move to the top, so the
    statement stays one flat WITH list rather than a CTE nested inside another."""
    ctes = query.args.get("with_")
    query.set("with_", None)
    outer = exp.select(
        *[exp.column(name, table="answer") for name in columns],
        *[exp.column(name, table="totals") for name in total_columns],
    )
    outer = outer.from_("answer").join(exp.Join(this=exp.to_table("totals"), kind="CROSS"))
    for cte in ctes.expressions if ctes is not None else ():
        outer = outer.with_(cte.alias, as_=cte.this)
    return outer.with_("answer", as_=query).with_("totals", as_=totals), columns + total_columns


def _build_scan(
    manifest: SemanticManifest,
    resolved: Resolved,
    measure: Measure,
    parameters: dict[str, object],
    *,
    extra_filters: tuple[ResolvedFilter, ...] = (),
    start_name: str = "start_date",
    start_value: date | None = None,
    include_dimensions: bool = True,
) -> _Scan:
    base = resolved.base_model
    alias = base.name
    business_dimension = next(
        d
        for d in base.dimensions
        if d.name == (measure.agg_time_dimension or resolved.partition.name)
    )
    business = exp.column(business_dimension.expr, table=alias)

    parameters.setdefault(start_name, start_value or resolved.date_range.start_date)
    parameters.setdefault("end_date", resolved.date_range.end_date + timedelta(days=1))
    conditions = [
        exp.GTE(this=business, expression=_placeholder(start_name)),
        exp.LT(this=business, expression=_placeholder("end_date")),
    ]

    partition = resolved.partition
    if measure.agg_time_dimension and measure.agg_time_dimension != partition.name:
        lag = timedelta(days=measure.partition_lag_days or 0)
        parameters.setdefault("partition_start", resolved.date_range.start_date - lag)
        parameters.setdefault(
            "partition_end", resolved.date_range.end_date + timedelta(days=1) + lag
        )
        partition_column = exp.column(partition.expr, table=alias)
        conditions += [
            exp.GTE(this=partition_column, expression=_placeholder("partition_start")),
            exp.LT(this=partition_column, expression=_placeholder("partition_end")),
        ]

    for index, filter_ in enumerate(resolved.metric_filters):
        column = _column_of(resolved, filter_.dimension)
        conditions.append(_predicate(column, filter_, f"metric_filter_{index}", parameters))
    for index, filter_ in enumerate(extra_filters):
        column = _column_of(resolved, filter_.dimension)
        conditions.append(_predicate(column, filter_, f"leg_filter_{index}", parameters))
    for index, filter_ in enumerate(resolved.where_filters):
        column = _column_of(resolved, filter_.dimension)
        conditions.append(_predicate(column, filter_, f"filter_{index}", parameters))

    joins: dict[str, str] = {}
    everything = (
        *(resolved.dimensions if include_dimensions else ()),
        *resolved.metric_filters,
        *extra_filters,
        *resolved.where_filters,
    )
    for item in everything:
        dimension = item if hasattr(item, "entity") else item.dimension
        if dimension is not None and dimension.entity:
            joins[dimension.entity] = dimension.model
    return _Scan(base=base, alias=alias, business=business, conditions=conditions, joins=joins)


def _apply(query: exp.Select, manifest: SemanticManifest, scan: _Scan) -> exp.Select:
    query = query.from_(exp.to_table(scan.base.table).as_(scan.alias))
    for entity in sorted(scan.joins):
        query = query.join(_join(scan.base, manifest.semantic_models[scan.joins[entity]], entity))
    condition = scan.conditions[0]
    for extra in scan.conditions[1:]:
        condition = exp.and_(condition, extra)
    return query.where(condition)


def _bucket(resolved: Resolved, business: exp.Column) -> exp.Expression | None:
    if not resolved.time_grain:
        return None
    return exp.DateTrunc(this=business, unit=exp.Literal.string(resolved.time_grain))


def _leg_filters(
    manifest: SemanticManifest, resolved: Resolved, metric: Metric
) -> tuple[ResolvedFilter, ...]:
    """A ratio leg carries its own definition: `web_revenue / order_count` filters only the top."""
    catalog = dimension_catalog(manifest.semantic_models, manifest.joins, resolved.base_model)
    filters = []
    for declared in metric.filters:
        entry = resolve(catalog, declared.field)
        if entry is not None and not isinstance(entry, list):
            filters.append(ResolvedFilter(declared.field, declared.operator, declared.value, entry))
    return tuple(filters)


def _compile_simple(
    manifest: SemanticManifest,
    resolved: Resolved,
    measure: Measure,
    dialect: str,
    parameters: dict[str, object],
) -> tuple[exp.Select, list[str]]:
    scan = _build_scan(manifest, resolved, measure, parameters)
    projections: list[exp.Expression] = []
    groups: list[exp.Expression] = []
    columns: list[str] = []

    bucket = _bucket(resolved, scan.business)
    if bucket is not None:
        projections.append(exp.alias_(bucket, "period"))
        groups.append(bucket)
        columns.append("period")
    for dimension in resolved.dimensions:
        column = _column_of(resolved, dimension)
        projections.append(exp.alias_(column, dimension.requested))
        groups.append(column)
        columns.append(dimension.requested)

    projections.append(exp.alias_(_aggregate(measure, scan.alias, dialect), resolved.metric.name))
    columns.append(resolved.metric.name)

    query = _apply(exp.select(*projections), manifest, scan)
    if groups:
        query = query.group_by(*groups)
    for index, clause in enumerate(resolved.having_filters, start=len(resolved.where_filters)):
        aggregate = _aggregate(measure, scan.alias, dialect)
        query = query.having(_predicate(aggregate, clause, f"filter_{index}", parameters))
    if not scan.joins:
        return query, columns
    totals, total_columns = _totals(manifest, scan, measure, dialect)
    return _with_totals(query, columns, totals, total_columns)


def _compile_snapshot(
    manifest: SemanticManifest,
    resolved: Resolved,
    measure: Measure,
    dialect: str,
    parameters: dict[str, object],
) -> tuple[exp.Select, list[str]]:
    """One row per group per period, chosen by the declared window, then aggregated.

    Summing a snapshot across time counts the same stock once per period. The declaration says which
    snapshot stands for the period, so the roll-up is the author's rather than a guess.
    """
    declared = measure.non_additive_dimension
    scan = _build_scan(manifest, resolved, measure, parameters)
    snapshot_dimension = next(d for d in scan.base.dimensions if d.name == declared.name)
    snapshot_column = exp.column(snapshot_dimension.expr, table=scan.alias)

    inner: list[exp.Expression] = []
    keys: list[str] = []
    partition_by: list[exp.Expression] = []

    bucket = _bucket(resolved, scan.business)
    if bucket is not None:
        inner.append(exp.alias_(bucket, "period"))
        partition_by.append(bucket)
        keys.append("period")
    for dimension in resolved.dimensions:
        inner.append(exp.alias_(_column_of(resolved, dimension), dimension.requested))
        keys.append(dimension.requested)
    for grouping in declared.window_groupings:
        entity = next(e for e in scan.base.entities if e.name == grouping)
        partition_by.append(exp.column(entity.expr, table=scan.alias))

    inner.append(
        exp.alias_(_qualify(parse_one(measure.expr, dialect=dialect), scan.alias), "value")
    )
    for entity in sorted(scan.joins):
        inner.append(
            exp.alias_(_null_key_case(scan.base, entity, scan.alias), f"{entity}__null_key")
        )
        unmatched = exp.Case(
            ifs=[exp.If(this=_unmatched(manifest, scan, entity), true=exp.Literal.number(1))],
            default=exp.Literal.number(0),
        )
        inner.append(exp.alias_(unmatched, f"{entity}__unmatched"))
    window = exp.Window(
        this=exp.RowNumber(),
        partition_by=partition_by,
        order=exp.Order(
            expressions=[exp.Ordered(this=snapshot_column, desc=declared.window_choice == "max")]
        ),
    )
    inner.append(exp.alias_(window, "position"))
    ranked = _apply(exp.select(*inner), manifest, scan)

    outer: list[exp.Expression] = [exp.column(key) for key in keys]
    outer.append(
        exp.alias_(_AGGREGATES[measure.agg](this=exp.column("value")), resolved.metric.name)
    )
    columns = [*keys, resolved.metric.name]

    query = (
        exp.select(*outer)
        .from_("ranked")
        .where(exp.EQ(this=exp.column("position"), expression=exp.Literal.number(1)))
    )
    if keys:
        query = query.group_by(*[exp.column(key) for key in keys])
    if not scan.joins:
        return query.with_("ranked", as_=ranked), columns

    # The totals read only the rows chosen as each period's snapshot: those are what the answer
    # adds up, and a mid-month snapshot of an unknown product is not month-end stock.
    chosen = exp.EQ(this=exp.column("position"), expression=exp.Literal.number(1))
    totals_projections: list[exp.Expression] = []
    total_columns: list[str] = []
    for entity in sorted(scan.joins):
        flagged = exp.EQ(this=exp.column(f"{entity}__unmatched"), expression=exp.Literal.number(1))
        value = exp.Case(ifs=[exp.If(this=flagged, true=exp.column("value"))])
        totals_projections += [
            exp.alias_(
                exp.Sum(this=exp.column(f"{entity}__unmatched")), f"{entity}__unreconciled_rows"
            ),
            exp.alias_(
                exp.Sum(this=exp.column(f"{entity}__null_key")), f"{entity}__empty_key_rows"
            ),
            exp.alias_(_AGGREGATES[measure.agg](this=value), f"{entity}__unreconciled_value"),
        ]
        total_columns += [f"{entity}{suffix}" for suffix in UNRECONCILED_SUFFIXES]
    totals = exp.select(*totals_projections).from_("ranked").where(chosen)
    return _with_totals(query.with_("ranked", as_=ranked), columns, totals, total_columns)


def _interval(count: int, granularity: str) -> exp.Expression:
    return exp.var(granularity.upper()), exp.Literal.number(count)


def _compile_cumulative(
    manifest: SemanticManifest,
    resolved: Resolved,
    measure: Measure,
    dialect: str,
    parameters: dict[str, object],
) -> tuple[exp.Select, list[str], date]:
    """Anchor periods joined to the rows their window covers.

    A window frame would be shorter, but `RANGE BETWEEN INTERVAL` is not supported by BigQuery or
    Snowflake even though sqlglot will happily emit it — plausible SQL that fails, or worse, means
    something else. Joins, comparisons and date arithmetic are the same everywhere.
    """
    metric = resolved.metric
    grain = resolved.time_grain
    end_exclusive = resolved.date_range.end_date + timedelta(days=1)
    raw_value = _qualify(parse_one(measure.expr, dialect=dialect), resolved.base_model.name)

    if grain is None:
        if metric.window:
            window_start = _shift(end_exclusive, -metric.window.count, metric.window.granularity)
        else:
            window_start = _truncate(resolved.date_range.end_date, metric.grain_to_date)
        scan = _build_scan(manifest, resolved, measure, parameters, start_value=window_start)
        projections = [
            exp.alias_(_column_of(resolved, d), d.requested) for d in resolved.dimensions
        ]
        projections.append(exp.alias_(_aggregate(measure, scan.alias, dialect), metric.name))
        query = _apply(exp.select(*projections), manifest, scan)
        if resolved.dimensions:
            query = query.group_by(*[_column_of(resolved, d) for d in resolved.dimensions])
        columns = [*(d.requested for d in resolved.dimensions), metric.name]
        if scan.joins:
            totals, total_columns = _totals(manifest, scan, measure, dialect)
            query, columns = _with_totals(query, columns, totals, total_columns)
        return query, columns, window_start

    anchors = _periods_in(resolved.date_range.start_date, end_exclusive, grain)
    if metric.window:
        count, unit = metric.window.count, metric.window.granularity
        scan_start = (
            _shift(anchors[0], -(count - 1), unit)
            if unit == grain
            else _shift(_shift(anchors[0], -count, unit), 1, grain)
        )
    else:
        scan_start = _truncate(anchors[0], metric.grain_to_date)

    period_scan = _build_scan(manifest, resolved, measure, parameters, include_dimensions=False)
    bucket = _bucket(resolved, period_scan.business)
    periods = _apply(exp.select(exp.alias_(bucket, "period")).distinct(), manifest, period_scan)

    measured_scan = _build_scan(
        manifest, resolved, measure, parameters, start_name="scan_start", start_value=scan_start
    )
    measured_projections: list[exp.Expression] = [
        exp.alias_(measured_scan.business, "occurred_at"),
        *[exp.alias_(_column_of(resolved, d), d.requested) for d in resolved.dimensions],
        exp.alias_(raw_value, "value"),
    ]
    measured = _apply(exp.select(*measured_projections), manifest, measured_scan)

    period_column = exp.column("period", table="periods")
    occurred = exp.column("occurred_at", table="measured")
    if metric.window and metric.window.granularity == grain:
        unit, amount = _interval(metric.window.count - 1, metric.window.granularity)
        window_start = exp.DateSub(this=period_column, expression=amount, unit=unit)
    elif metric.window:
        unit, amount = _interval(metric.window.count, metric.window.granularity)
        back = exp.DateSub(this=period_column, expression=amount, unit=unit)
        step_unit, one = _interval(1, grain)
        window_start = exp.DateAdd(this=back, expression=one, unit=step_unit)
    else:
        window_start = period_column
    step_unit, one = _interval(1, grain)
    next_period = exp.DateAdd(this=period_column, expression=one, unit=step_unit)

    outer: list[exp.Expression] = [exp.alias_(period_column, "period")]
    groups: list[exp.Expression] = [period_column]
    columns = ["period"]
    for dimension in resolved.dimensions:
        carried = exp.column(dimension.requested, table="measured")
        outer.append(exp.alias_(carried, dimension.requested))
        groups.append(carried)
        columns.append(dimension.requested)
    outer.append(
        exp.alias_(
            _AGGREGATES[measure.agg](this=exp.column("value", table="measured")), metric.name
        )
    )
    columns.append(metric.name)

    condition = exp.and_(
        exp.GTE(this=occurred, expression=window_start),
        exp.LT(this=occurred, expression=next_period),
    )
    query = (
        exp.select(*outer)
        .from_("periods")
        .join(exp.Join(this=exp.to_table("measured"), on=condition))
        .group_by(*groups)
    )
    query = query.with_("periods", as_=periods).with_("measured", as_=measured)
    if measured_scan.joins:
        # Over the lookback too: an unmatched row from before the range still shapes the answer.
        totals, total_columns = _totals(manifest, measured_scan, measure, dialect)
        query, columns = _with_totals(query, columns, totals, total_columns)
    return query, columns, scan_start


def _compile_ratio(
    manifest: SemanticManifest, resolved: Resolved, dialect: str, parameters: dict[str, object]
) -> tuple[exp.Select, list[str]]:
    """Divide aggregates after grouping. Summing ratios answers a different question."""
    keys: list[str] = ["period"] if resolved.time_grain else []
    keys += [d.requested for d in resolved.dimensions]

    legs: dict[str, exp.Select] = {}
    leg_totals: dict[str, tuple[exp.Select, list[str]]] = {}
    sides = (("numerator", resolved.metric.numerator), ("denominator", resolved.metric.denominator))
    for side, name in sides:
        leg_metric = manifest.metrics[name]
        measure = metric_sources(manifest.semantic_models, manifest.metrics, leg_metric)[0][0]
        scan = _build_scan(
            manifest,
            resolved,
            measure,
            parameters,
            extra_filters=_leg_filters(manifest, resolved, leg_metric),
        )
        projections: list[exp.Expression] = []
        groups: list[exp.Expression] = []
        bucket = _bucket(resolved, scan.business)
        if bucket is not None:
            projections.append(exp.alias_(bucket, "period"))
            groups.append(bucket)
        for dimension in resolved.dimensions:
            column = _column_of(resolved, dimension)
            projections.append(exp.alias_(column, dimension.requested))
            groups.append(column)
        projections.append(exp.alias_(_aggregate(measure, scan.alias, dialect), "value"))
        leg = _apply(exp.select(*projections), manifest, scan)
        legs[side] = leg.group_by(*groups) if groups else leg
        if scan.joins:
            leg_totals[side] = _totals(manifest, scan, measure, dialect, label=f"__{side}")

    ratio = exp.Div(
        this=exp.func("COALESCE", exp.column("value", table="numerator"), exp.Literal.number(0)),
        expression=exp.func(
            "NULLIF", exp.column("value", table="denominator"), exp.Literal.number(0)
        ),
    )
    outer = [exp.alias_(exp.column(key, table="denominator"), key) for key in keys]
    outer.append(exp.alias_(ratio, resolved.metric.name))

    query = exp.select(*outer).from_("denominator")
    if keys:
        condition = None
        for key in keys:
            # NULL-safe: a blank group is a group. With `=`, `NULL = NULL` is never true, its
            # numerator is lost, and COALESCE reports the ratio as zero.
            equality = exp.NullSafeEQ(
                this=exp.column(key, table="denominator"),
                expression=exp.column(key, table="numerator"),
            )
            condition = equality if condition is None else exp.and_(condition, equality)
        query = query.join(exp.Join(this=exp.to_table("numerator"), on=condition, side="LEFT"))
    else:
        query = query.join(exp.Join(this=exp.to_table("numerator"), kind="CROSS"))

    query = query.with_("numerator", as_=legs["numerator"])
    query = query.with_("denominator", as_=legs["denominator"])
    columns = [*keys, resolved.metric.name]
    if not leg_totals:
        return query, columns

    # Each half is accounted for over its own scan: the halves can filter different rows, so one
    # count for the ratio would be true of one half and false of the other.
    carried: list[exp.Expression] = []
    total_columns: list[str] = []
    for side, (totals, names) in leg_totals.items():
        query = query.with_(f"{side}_totals", as_=totals)
        carried += [exp.column(name, table=f"{side}_totals") for name in names]
        total_columns += names
    tables = [f"{side}_totals" for side in leg_totals]
    combined = exp.select(*carried).from_(tables[0])
    for table in tables[1:]:
        combined = combined.join(exp.Join(this=exp.to_table(table), kind="CROSS"))
    return _with_totals(query, columns, combined, total_columns)


def compile_query(
    manifest: SemanticManifest, resolved: Resolved, dialect: str = "duckdb"
) -> CompiledQuery:
    """Return one statement and its parameters. Pure: the same request compiles identically."""
    if dialect not in DIALECTS:
        raise ValueError(f"unsupported dialect {dialect!r}; expected one of {', '.join(DIALECTS)}")

    parameters: dict[str, object] = {}
    end_exclusive = resolved.date_range.end_date + timedelta(days=1)
    scan_start = resolved.date_range.start_date
    if resolved.metric.type == "ratio":
        query, columns = _compile_ratio(manifest, resolved, dialect, parameters)
    else:
        measure = metric_sources(manifest.semantic_models, manifest.metrics, resolved.metric)[0][0]
        if resolved.metric.type == "cumulative":
            query, columns, scan_start = _compile_cumulative(
                manifest, resolved, measure, dialect, parameters
            )
        elif measure.non_additive_dimension is not None:
            query, columns = _compile_snapshot(manifest, resolved, measure, dialect, parameters)
        else:
            query, columns = _compile_simple(manifest, resolved, measure, dialect, parameters)

    # The limit keeps whichever rows sort first, so the order is always total: the request's own
    # order, then every group key, with NULL last on every engine.
    requested = [name for name, _ in resolved.order_by]
    keys = [
        c for c in columns if c != resolved.metric.name and not c.endswith(UNRECONCILED_SUFFIXES)
    ]
    ordering = [*resolved.order_by, *((key, "asc") for key in keys if key not in requested)]
    for name, direction in ordering:
        query = query.order_by(
            exp.Ordered(this=exp.column(name), desc=direction == "desc", nulls_first=False)
        )
    query = query.limit(resolved.row_limit)
    return CompiledQuery(
        sql=query.sql(dialect=dialect),
        parameters=parameters,
        dialect=dialect,
        columns=columns,
        output_window=(resolved.date_range.start_date, end_exclusive),
        scan_window=(scan_start, end_exclusive),
        expected_periods=(
            _periods_in(resolved.date_range.start_date, end_exclusive, resolved.time_grain)
            if resolved.time_grain
            else []
        ),
    )
