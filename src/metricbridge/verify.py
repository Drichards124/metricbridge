"""Check the manifest against the warehouse it describes, before anything is served.

A manifest is a claim about a warehouse. A column it names may not exist, which fails loudly on the
first query that reads it. A key it declares unique may repeat, which fails silently: the "N:1" join
fans out and every measure over it inflates. So both claims are checked when an engine is attached,
and a manifest the warehouse contradicts is refused, just like a manifest that contradicts itself.

Every statement here is built from the manifest alone, never from a request, which is why none of
them passes through the request guardrail. They read no rows, except to count repeated keys — the
one check that must scan a whole table, because a key is only unique over all of it. Only joined-to
keys are checked: a repeated key nothing joins to cannot fan anything out.
"""

from sqlglot import exp, parse_one

from .contract import RefusalError
from .engine import Engine
from .manifest import ManifestError, ManifestIssue, SemanticManifest


def _run(engine: Engine, statement: exp.Expression) -> tuple[list[tuple], str | None]:
    """The rows, or the engine's own fixed refusal message: never the driver's text."""
    try:
        return engine.execute(statement.sql(dialect=engine.dialect), {}), None
    except RefusalError as refused:
        return [], refused.refusals[0].message


def _ambiguous_schemas(manifest: SemanticManifest, engine: Engine) -> set[str]:
    """DuckDB names a database after its file, so `storefront.duckdb` makes `storefront.<table>`
    ambiguous between the database and the schema. Every read fails, and the log says only
    BinderException."""
    if engine.dialect != "duckdb":
        return set()
    rows, _ = _run(
        engine, parse_one("SELECT database_name FROM duckdb_databases() WHERE NOT internal")
    )
    schemas = {
        m.table.split(".")[0] for m in manifest.semantic_models.values() if m.table.count(".") == 1
    }
    return schemas & {name for (name,) in rows}


def verify(manifest: SemanticManifest, engine: Engine) -> None:
    """Raise `ManifestError` with every way the warehouse contradicts the manifest."""
    issues: list[ManifestIssue] = []
    unreadable: set[str] = set()
    broken_keys: set[tuple[str, str]] = set()

    ambiguous = _ambiguous_schemas(manifest, engine)
    for schema in sorted(ambiguous):
        issues.append(
            ManifestIssue(
                "",
                "",
                f"the database is named {schema!r}, like the schema {schema!r}, so every "
                f"{schema}.<table> reference is ambiguous. DuckDB names a database after its "
                "file: rename the file.",
            )
        )

    models = sorted(manifest.semantic_models.values(), key=lambda m: m.name)
    for model in models:
        table = exp.to_table(model.table)
        if model.table.count(".") == 1 and model.table.split(".")[0] in ambiguous:
            unreadable.add(model.name)
            continue
        _, problem = _run(engine, exp.select(exp.Literal.number(1)).from_(table.copy()).limit(0))
        if problem is not None:
            unreadable.add(model.name)
            issues.append(
                ManifestIssue("", model.name, f"table {model.table!r} could not be read: {problem}")
            )
            continue
        for kind, elements in (
            ("entities", model.entities),
            ("dimensions", model.dimensions),
            ("measures", model.measures),
        ):
            for element in elements:
                expression = parse_one(element.expr, dialect=engine.dialect)
                probe = exp.select(expression).from_(table.copy()).limit(0)
                _, problem = _run(engine, probe)
                if problem is None:
                    continue
                if kind == "entities":
                    broken_keys.add((model.name, element.name))
                issues.append(
                    ManifestIssue(
                        "",
                        f"{model.name}.{kind}.{element.name}",
                        f"{element.expr!r} could not be evaluated on {model.table!r}: {problem}",
                    )
                )

    for model_name, entity_name in sorted({(j.to_model, j.entity) for j in manifest.joins}):
        if model_name in unreadable or (model_name, entity_name) in broken_keys:
            continue  # already reported
        model = manifest.semantic_models[model_name]
        entity = next(e for e in model.entities if e.name == entity_name)
        key = parse_one(entity.expr, dialect=engine.dialect)
        # A NULL key matches nothing in a join, so repeating it fans nothing out.
        repeated = (
            exp.select(key.copy())
            .from_(exp.to_table(model.table))
            .where(exp.Not(this=exp.Is(this=key.copy(), expression=exp.Null())))
            .group_by(key.copy())
            .having(exp.GT(this=exp.Count(this=exp.Star()), expression=exp.Literal.number(1)))
        )
        count = exp.select(exp.Count(this=exp.Star())).from_(repeated.subquery("repeated"))
        rows, problem = _run(engine, count)
        path = f"{model_name}.entities.{entity_name}"
        if problem is not None:
            issues.append(
                ManifestIssue(
                    "", path, f"could not check that {entity_name!r} is unique: {problem}"
                )
            )
        elif rows[0][0]:
            # The count, never the values: key values are data, and data stays out of messages.
            issues.append(
                ManifestIssue(
                    "",
                    path,
                    f"entity {entity_name!r} is declared {entity.type}, but {rows[0][0]} value(s) "
                    f"of {entity.expr!r} appear more than once in {model.table!r}. Every join to "
                    f"{model_name!r} would fan out and inflate the measures over it: fix the data "
                    "or the declaration.",
                )
            )

    if issues:
        raise ManifestError(issues)
