"""Compile a validated request into parameterized SQL.

**The agent never writes SQL and never sees a database.** It sends a structured request; this
builds the statement. That inversion is the firewall: there is no prompt-injection path into
the query text, because the query text is not agent-supplied.

Values travel as bound parameters, never interpolated. A dimension value like
`O'Brien'; DROP TABLE` is a string to the driver and nothing to the parser.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp, parse_one

from .contract import QueryRequest
from .manifest import Metric

PERIOD_ALIAS = "period"


def _placeholder() -> exp.Placeholder:
    return exp.Placeholder()


def _column(table: str, column: str) -> exp.Column:
    return exp.column(column, table=table.split(".")[-1])


def compile_query(metric: Metric, request: QueryRequest, dialect: str = "duckdb") -> tuple[str, list[Any]]:
    """Return (sql, params). Pure: same request in, same statement out."""
    table_alias = metric.table.split(".")[-1]
    params: list[Any] = []
    projections: list[exp.Expression] = []
    groups: list[exp.Expression] = []

    if request.time_grain:
        bucket = exp.Anonymous(
            this="DATE_TRUNC",
            expressions=[exp.Literal.string(request.time_grain),
                         _column(metric.table, metric.partition_column)])
        projections.append(exp.alias_(bucket, PERIOD_ALIAS))
        groups.append(bucket)

    for name in request.dimensions:
        dimension = metric.dimension(name)
        assert dimension is not None  # validate() guarantees this
        column = _column(metric.table, dimension.column)
        projections.append(exp.alias_(column, name))
        groups.append(column)

    measure = parse_one(metric.expression, dialect=dialect)
    projections.append(exp.alias_(measure, metric.name))

    # Partition pruning is not optional and not the agent's choice: it is added here, always.
    partition = _column(metric.table, metric.partition_column)
    where: exp.Expression = exp.Between(
        this=partition, low=_placeholder(), high=_placeholder())
    params.extend([request.date_range.start_date.isoformat(),
                   request.date_range.end_date.isoformat()])

    for filter_ in request.filters:
        dimension = metric.dimension(filter_.field)
        assert dimension is not None
        column = _column(metric.table, dimension.column)
        predicate = _predicate(column, filter_.operator, filter_.value, params)
        where = exp.and_(where, predicate)

    query = (
        exp.select(*projections)
        .from_(exp.to_table(metric.table).as_(table_alias))
        .where(where)
    )
    if groups:
        query = query.group_by(*groups)
    for clause in request.order_by:
        query = query.order_by(exp.ordered(exp.column(clause.field), desc=clause.direction == "desc"))
    query = query.limit(request.row_limit)
    return query.sql(dialect=dialect, pretty=True), params


def _predicate(column: exp.Column, operator: str, value: Any, params: list[Any]) -> exp.Expression:
    if operator == "IS NULL":
        return exp.Is(this=column, expression=exp.Null())
    if operator == "IS NOT NULL":
        return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
    if operator in ("IN", "NOT IN"):
        values = value if isinstance(value, list) else [value]
        params.extend(values)
        predicate = exp.In(this=column, expressions=[_placeholder() for _ in values])
        return exp.Not(this=predicate) if operator == "NOT IN" else predicate
    params.append(value)
    builder = {"=": exp.EQ, "!=": exp.NEQ, ">": exp.GT, ">=": exp.GTE,
               "<": exp.LT, "<=": exp.LTE, "LIKE": exp.Like}[operator]
    return builder(this=column, expression=_placeholder())
