"""Compile a validated request into one parameterised statement.

The agent never writes SQL and never sees a database: it sends a structured request and this builds
the statement. That inversion is the firewall — there is no prompt-injection path into query text,
because query text is not agent-supplied. Values travel as bound parameters, so a filter value like
`O'Brien'; DROP TABLE` reaches the database as a string and nothing else.

Everything here is built from typed sqlglot expressions rather than assembled strings, which is why
a time bucket comes out as `DATE_TRUNC(col, MONTH)` on BigQuery and `dateTrunc(...)` on ClickHouse
instead of one spelling that is wrong on four engines.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlglot import exp, parse_one

from .contract import Resolved, ResolvedFilter
from .manifest import Measure, SemanticManifest, SemanticModel, metric_sources

DIALECTS = ("duckdb", "postgres", "bigquery", "snowflake", "clickhouse")

_AGGREGATES = {
    "sum": exp.Sum,
    "count": exp.Count,
    "min": exp.Min,
    "max": exp.Max,
    "average": exp.Avg,
}
_COMPARISONS = {"=": exp.EQ, "!=": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: dict[str, object]
    dialect: str
    columns: list[str]


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


def _dimension_column(resolved: Resolved, dimension) -> exp.Column:
    table = dimension.entity or resolved.base_model.name
    return exp.column(dimension.column, table=table)


def _join(base: SemanticModel, target: SemanticModel, entity: str) -> exp.Join:
    """Always LEFT: an inner join silently drops null-key rows, shrinking the denominator."""
    foreign = next(e for e in base.entities if e.name == entity)
    unique = next(e for e in target.entities if e.name == entity)
    condition = exp.EQ(
        this=exp.column(foreign.expr, table=base.name),
        expression=exp.column(unique.expr, table=entity),
    )
    return exp.Join(this=exp.to_table(target.table).as_(entity), on=condition, side="LEFT")


def compile_query(
    manifest: SemanticManifest, resolved: Resolved, dialect: str = "duckdb"
) -> CompiledQuery:
    """Return one statement and its parameters. Pure: the same request compiles identically."""
    if dialect not in DIALECTS:
        raise ValueError(f"unsupported dialect {dialect!r}; expected one of {', '.join(DIALECTS)}")

    base = resolved.base_model
    alias = base.name
    measures, _ = metric_sources(manifest.semantic_models, manifest.metrics, resolved.metric)
    measure = measures[0]
    parameters: dict[str, object] = {}

    business = next(
        d
        for d in base.dimensions
        if d.name == (measure.agg_time_dimension or resolved.partition.name)
    )
    business_column = exp.column(business.expr, table=alias)

    projections: list[exp.Expression] = []
    groups: list[exp.Expression] = []
    columns: list[str] = []

    if resolved.time_grain:
        bucket = exp.DateTrunc(this=business_column, unit=exp.Literal.string(resolved.time_grain))
        projections.append(exp.alias_(bucket, "period"))
        groups.append(bucket)
        columns.append("period")

    for dimension in resolved.dimensions:
        column = _dimension_column(resolved, dimension)
        projections.append(exp.alias_(column, dimension.requested))
        groups.append(column)
        columns.append(dimension.requested)

    projections.append(exp.alias_(_aggregate(measure, alias, dialect), resolved.metric.name))
    columns.append(resolved.metric.name)

    reached: dict[str, str] = {}  # entity -> the semantic model it reaches
    for item in (*resolved.dimensions, *resolved.metric_filters, *resolved.where_filters):
        dimension = item if hasattr(item, "entity") else item.dimension
        if dimension is not None and dimension.entity:
            reached[dimension.entity] = dimension.model
    entities = sorted(reached)
    for entity in entities:
        foreign = next(e for e in base.entities if e.name == entity)
        null_keys = exp.Sum(
            this=exp.Case(
                ifs=[
                    exp.If(
                        this=exp.Is(
                            this=exp.column(foreign.expr, table=alias), expression=exp.Null()
                        ),
                        true=exp.Literal.number(1),
                    )
                ],
                default=exp.Literal.number(0),
            )
        )
        name = f"{entity}__null_key_rows"
        projections.append(exp.alias_(null_keys, name))
        columns.append(name)

    parameters["start_date"] = resolved.date_range.start_date
    parameters["end_date"] = resolved.date_range.end_date + timedelta(days=1)
    conditions: list[exp.Expression] = [
        exp.GTE(this=business_column, expression=_placeholder("start_date")),
        exp.LT(this=business_column, expression=_placeholder("end_date")),
    ]

    partition = resolved.partition
    if measure.agg_time_dimension and measure.agg_time_dimension != partition.name:
        lag = timedelta(days=measure.partition_lag_days or 0)
        parameters["partition_start"] = resolved.date_range.start_date - lag
        parameters["partition_end"] = resolved.date_range.end_date + timedelta(days=1) + lag
        partition_column = exp.column(partition.expr, table=alias)
        conditions += [
            exp.GTE(this=partition_column, expression=_placeholder("partition_start")),
            exp.LT(this=partition_column, expression=_placeholder("partition_end")),
        ]

    for index, filter_ in enumerate(resolved.metric_filters):
        column = _dimension_column(resolved, filter_.dimension)
        conditions.append(_predicate(column, filter_, f"metric_filter_{index}", parameters))
    for index, filter_ in enumerate(resolved.where_filters):
        column = _dimension_column(resolved, filter_.dimension)
        conditions.append(_predicate(column, filter_, f"filter_{index}", parameters))

    query = exp.select(*projections).from_(exp.to_table(base.table).as_(alias))
    for entity in entities:
        query = query.join(_join(base, manifest.semantic_models[reached[entity]], entity))

    condition = conditions[0]
    for extra in conditions[1:]:
        condition = exp.and_(condition, extra)
    query = query.where(condition)

    if groups:
        query = query.group_by(*groups)

    for index, clause in enumerate(resolved.having_filters, start=len(resolved.where_filters)):
        aggregate = _aggregate(measure, alias, dialect)
        query = query.having(_predicate(aggregate, clause, f"filter_{index}", parameters))

    for field, direction in resolved.order_by:
        query = query.order_by(exp.Ordered(this=exp.column(field), desc=direction == "desc"))

    query = query.limit(resolved.row_limit)
    return CompiledQuery(
        sql=query.sql(dialect=dialect),
        parameters=parameters,
        dialect=dialect,
        columns=columns,
    )
