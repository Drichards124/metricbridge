"""Assertions on the syntax tree, run on the statement we are about to execute.

We check SQL we generated ourselves, on purpose. That looks redundant until you notice the compiler
is the component most likely to contain the bug — a mishandled filter, a dropped predicate on an
edge case. Defence in depth means the last thing before the warehouse validates the artifact rather
than trusting the process that produced it.

Checking the tree rather than the text is what makes it exact. String matching for `WHERE` or
`LIMIT` fails on the first comment, quoted literal or subquery, and it fails *open*.
"""

from sqlglot import exp, parse

from .contract import MAX_ROW_LIMIT, Refusal, RefusalError
from .manifest import SemanticManifest, SemanticModel

CODE = "guardrail_violation"

# Anything that is not a read. A gateway holding warehouse credentials never emits these.
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Grant,
)
_BOUNDS = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)


def _refuse(message: str, remediation: str) -> RefusalError:
    return RefusalError([Refusal(code=CODE, message=message, field="sql", remediation=remediation)])


def _table_name(table: exp.Table) -> str:
    return ".".join(part for part in (table.catalog, table.db, table.name) if part)


def _models_by_table(manifest: SemanticManifest) -> dict[str, SemanticModel]:
    return {model.table: model for model in manifest.semantic_models.values()}


def _time_columns(model: SemanticModel) -> set[str]:
    return {d.expr for d in model.dimensions if d.type == "time"}


def _direct_tables(select: exp.Select) -> list[exp.Table]:
    """Only the tables this select reads itself.

    Walking the whole subtree would blame an outer query for a CTE's scan, and the CTE is checked
    on its own terms a moment later.
    """
    from_ = select.args.get("from_") or select.args.get("from")  # sqlglot 30 renamed this arg
    sources = [from_.this] if from_ is not None else []
    sources += [join.this for join in select.args.get("joins") or []]
    return [source for source in sources if isinstance(source, exp.Table)]


def _bounded(select: exp.Select, alias: str, columns: set[str]) -> bool:
    """Is some scan of this table bounded on one of its own time columns?"""
    where = select.args.get("where")
    if where is None:
        return False
    for comparison in where.find_all(*_BOUNDS):
        for column in comparison.find_all(exp.Column):
            if column.name in columns and (not column.table or column.table == alias):
                return True
    return False


def assert_safe(sql: str, manifest: SemanticManifest, *, dialect: str = "duckdb") -> None:
    """Raise `RefusalError` unless this statement is one bounded read of declared tables."""
    statements = [statement for statement in parse(sql, dialect=dialect) if statement is not None]
    if len(statements) != 1:
        raise _refuse(
            f"{len(statements)} statements found; only one statement may reach the warehouse.",
            "Send one request at a time; stacked statements are never generated.",
        )

    tree = statements[0]
    if isinstance(tree, _FORBIDDEN) or not isinstance(tree, exp.Select):
        raise _refuse(
            f"statement is {type(tree).__name__.upper()}, not a SELECT.",
            "MetricBridge only reads: no DDL or DML is ever compiled.",
        )
    for node in tree.find_all(*_FORBIDDEN):
        raise _refuse(
            f"statement contains {type(node).__name__.upper()}, which is not a SELECT.",
            "MetricBridge only reads: no DDL or DML is ever compiled.",
        )

    declared = _models_by_table(manifest)
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}

    for table in tree.find_all(exp.Table):
        name = _table_name(table)
        if name in cte_names or name in declared:
            continue
        raise _refuse(
            f"table {name!r} is not declared in the manifest.",
            "Every table read must belong to a semantic model; nothing else is reachable.",
        )

    for select in tree.find_all(exp.Select):
        if any(isinstance(projection, exp.Star) for projection in select.expressions):
            raise _refuse(
                "statement uses SELECT *, which reads columns no metric declared.",
                "Project the metric's own columns; SELECT * is never generated.",
            )
        for table in _direct_tables(select):
            model = declared.get(_table_name(table))
            if model is None or not model.measures:
                continue  # a dimension table is bounded by the fact it joins to
            alias = table.alias or table.name
            if not _bounded(select, alias, _time_columns(model)):
                raise _refuse(
                    f"scan of {model.table!r} is unbounded: no predicate on its time column.",
                    "Every scan must be bounded on a date; an unbounded scan is refused.",
                )

    limit = tree.args.get("limit")
    if limit is None:
        raise _refuse(
            "statement has no row limit.",
            f"A row limit is injected by the compiler and capped at {MAX_ROW_LIMIT}.",
        )
    requested = int(limit.expression.name)
    if requested > MAX_ROW_LIMIT:
        raise _refuse(
            f"row limit {requested} exceeds the ceiling of {MAX_ROW_LIMIT}.",
            f"Request at most {MAX_ROW_LIMIT} rows; the ceiling is enforced server-side.",
        )
