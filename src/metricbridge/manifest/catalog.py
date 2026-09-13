"""What a metric can reach: its own table's cuts, and those one join away.

This lives in the manifest package because both the loader and the contract need the same answer.
A metric-level filter is checked against it at load time; a request is checked against it per call.
Two implementations would drift, and the drift would show up as a manifest that loads but cannot be
queried.
"""

from dataclasses import dataclass

from .model import Measure, Metric, SemanticModel


@dataclass(frozen=True)
class CatalogEntry:
    """One reachable cut, under the name the signature advertises."""

    name: str  # qualified: `entity__dimension` when reached through a join
    model: str
    column: str
    entity: str | None
    kind: str


def metric_sources(
    models: dict[str, SemanticModel], metrics: dict[str, Metric], metric: Metric
) -> tuple[tuple[Measure, ...], tuple[SemanticModel, ...]]:
    """The measures a metric reads, and the semantic models that own them."""
    names: list[str] = []
    if metric.type == "ratio":
        for leg in (metric.numerator, metric.denominator):
            referenced = metrics.get(leg or "")
            if referenced is not None and referenced.measure is not None:
                names.append(referenced.measure)
    elif metric.measure is not None:
        names.append(metric.measure)

    measures: list[Measure] = []
    owners: list[SemanticModel] = []
    for name in names:
        for model in models.values():
            found = next((m for m in model.measures if m.name == name), None)
            if found is not None:
                measures.append(found)
                owners.append(model)
                break
    return tuple(measures), tuple(owners)


def dimension_catalog(
    models: dict[str, SemanticModel], joins, base: SemanticModel
) -> dict[str, CatalogEntry]:
    catalog: dict[str, CatalogEntry] = {
        dimension.name: CatalogEntry(
            name=dimension.name,
            model=base.name,
            column=dimension.expr,
            entity=None,
            kind=dimension.type,
        )
        for dimension in base.dimensions
    }
    for join in joins:
        if join.from_model != base.name:
            continue
        for dimension in models[join.to_model].dimensions:
            qualified = f"{join.entity}__{dimension.name}"
            catalog[qualified] = CatalogEntry(
                name=qualified,
                model=join.to_model,
                column=dimension.expr,
                entity=join.entity,
                kind=dimension.type,
            )
    return catalog


def resolve(catalog: dict[str, CatalogEntry], requested: str) -> CatalogEntry | list[str] | None:
    """The cut, or the qualified alternatives when a bare name is ambiguous, or None."""
    found = catalog.get(requested)
    if found is not None:
        return found
    candidates = [entry for name, entry in catalog.items() if name.endswith(f"__{requested}")]
    if not candidates:
        return None
    if len(candidates) > 1:
        return sorted(entry.name for entry in candidates)
    return candidates[0]
