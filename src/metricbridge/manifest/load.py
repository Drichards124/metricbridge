"""Load a directory of manifest files into one validated `SemanticManifest`, or refuse it.

Two passes. The structural pass validates every file against the model and collects every error.
Only a structurally valid manifest reaches the semantic pass — cross-file checks against a
half-parsed manifest would bury the real problem under phantom "unknown measure" reports.

A manifest is a claim about the warehouse. Nothing here connects to one: whether a declared key
is actually unique is verified when an engine is attached, not at load.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from .catalog import dimension_catalog, metric_sources, resolve
from .model import ManifestFile, Metric, SemanticModel

MANIFEST_SUFFIXES = (".yml", ".yaml")


@dataclass(frozen=True)
class ManifestIssue:
    file: str
    path: str
    message: str

    def __str__(self) -> str:
        where = ":".join(part for part in (self.file, self.path) if part)
        return f"{where}: {self.message}" if where else self.message


class ManifestError(Exception):
    """A manifest was refused. Carries every problem found, not just the first."""

    def __init__(self, issues: list[ManifestIssue]) -> None:
        self.issues = issues
        super().__init__(
            f"{len(issues)} manifest problem(s):\n" + "\n".join(f"  {i}" for i in issues)
        )


@dataclass(frozen=True)
class Join:
    from_model: str
    to_model: str
    entity: str
    cardinality: Literal["many_to_one", "one_to_one"]


@dataclass(frozen=True)
class SemanticManifest:
    semantic_models: dict[str, SemanticModel]
    metrics: dict[str, Metric]
    joins: tuple[Join, ...]
    version: str  # sha256 of the canonical model: changes with meaning, not with formatting


def load_manifest(path: str | Path) -> SemanticManifest:
    root = Path(path)
    if root.is_file():
        base, files = root.parent, [root]
    elif root.is_dir():
        base, files = (
            root,
            sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in MANIFEST_SUFFIXES),
        )
    else:
        base, files = root, []
    if not files:
        raise ManifestError([ManifestIssue("", "", f"no manifest files found at {root}")])

    parsed: list[tuple[str, ManifestFile]] = []
    issues: list[ManifestIssue] = []
    for file in files:
        name = file.relative_to(base).as_posix()
        document, problem = _read(file)
        if problem:
            issues.append(ManifestIssue(name, "", problem))
            continue
        try:
            parsed.append((name, ManifestFile.model_validate(document)))
        except ValidationError as error:
            issues.extend(ManifestIssue(name, _path(e["loc"]), _message(e)) for e in error.errors())
    if issues:
        raise ManifestError(issues)

    models = {m.name: m for _, f in parsed for m in f.semantic_models}
    metrics = {m.name: m for _, f in parsed for m in f.metrics}
    joins = _joins(models.values())

    issues = _semantic_issues(parsed) + _metric_filter_issues(parsed, models, metrics, joins)
    if issues:
        raise ManifestError(issues)

    return assemble(models, metrics)


def _read(file: Path) -> tuple[object, str | None]:
    try:
        document = yaml.safe_load(file.read_text())
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return None, f"invalid YAML: {getattr(error, 'problem', None) or error}{where}"
    if document is None:
        return {}, None
    if not isinstance(document, dict):
        return None, "the top level must be a mapping with semantic_models and/or metrics"
    return document, None


def _path(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _message(error: dict) -> str:
    if error["type"] == "extra_forbidden":
        return "unknown field"
    if error["type"] == "missing":
        return "field required"
    return error["msg"].removeprefix("Value error, ")


def _semantic_issues(parsed: list[tuple[str, ManifestFile]]) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    model_seen: dict[str, str] = {}
    measure_seen: dict[str, str] = {}
    entity_holders: dict[str, list[tuple[str, str, SemanticModel, int]]] = defaultdict(list)

    for file, document in parsed:
        for i, model in enumerate(document.semantic_models):
            at = f"semantic_models[{i}]"
            if model.name in model_seen:
                issues.append(
                    ManifestIssue(
                        file,
                        f"{at}.name",
                        f"semantic model {model.name!r} is defined more than once "
                        f"(first in {model_seen[model.name]})",
                    )
                )
            model_seen.setdefault(model.name, file)

            for kind, elements in (("entities", model.entities), ("dimensions", model.dimensions)):
                names: set[str] = set()
                for j, element in enumerate(elements):
                    if element.name in names:
                        issues.append(
                            ManifestIssue(
                                file,
                                f"{at}.{kind}[{j}].name",
                                f"{kind[:-1] if kind == 'dimensions' else 'entity'} "
                                f"{element.name!r} is defined more than once in "
                                f"semantic model {model.name!r}",
                            )
                        )
                    names.add(element.name)

            primaries = [e.name for e in model.entities if e.type == "primary"]
            if len(primaries) > 1:
                issues.append(
                    ManifestIssue(
                        file,
                        f"{at}.entities",
                        f"more than one primary entity ({', '.join(primaries)})",
                    )
                )
            for j, entity in enumerate(model.entities):
                entity_holders[entity.name].append((file, at, model, j))

            partitions = [d.name for d in model.dimensions if d.is_partition]
            if len(partitions) > 1:
                issues.append(
                    ManifestIssue(
                        file,
                        f"{at}.dimensions",
                        f"more than one partition dimension ({', '.join(partitions)})",
                    )
                )
            elif model.measures and not partitions:
                issues.append(
                    ManifestIssue(
                        file,
                        f"{at}.dimensions",
                        "no partition dimension: a model with measures needs exactly one "
                        "time dimension with is_partition: true",
                    )
                )

            partition = model.partition
            time_dimensions = {d.name for d in model.dimensions if d.type == "time"}
            entity_names = {e.name for e in model.entities}
            for j, measure in enumerate(model.measures):
                where = f"{at}.measures[{j}]"
                if measure.name in measure_seen:
                    issues.append(
                        ManifestIssue(
                            file,
                            f"{where}.name",
                            f"measure {measure.name!r} is defined more than once "
                            f"(first in {measure_seen[measure.name]})",
                        )
                    )
                measure_seen.setdefault(measure.name, f"semantic model {model.name!r}, {file}")
                if measure.agg_time_dimension is not None:
                    if measure.agg_time_dimension not in time_dimensions:
                        issues.append(
                            ManifestIssue(
                                file,
                                f"{where}.agg_time_dimension",
                                f"{measure.agg_time_dimension!r} must be a time dimension of "
                                f"semantic model {model.name!r}",
                            )
                        )
                    elif (
                        partition is not None
                        and measure.agg_time_dimension != partition.name
                        and measure.partition_lag_days is None
                    ):
                        issues.append(
                            ManifestIssue(
                                file,
                                f"{where}.partition_lag_days",
                                f"measure {measure.name!r} is aggregated by "
                                f"{measure.agg_time_dimension!r} but partitioned on "
                                f"{partition.name!r}: declare partition_lag_days so the "
                                f"scan can be bounded without dropping late rows",
                            )
                        )
                if measure.partition_lag_days is not None and measure.agg_time_dimension is None:
                    issues.append(
                        ManifestIssue(
                            file,
                            f"{where}.partition_lag_days",
                            "partition_lag_days only means something with agg_time_dimension: "
                            "without it the partition column is the business date",
                        )
                    )
                snapshot = measure.non_additive_dimension
                if snapshot is None:
                    continue
                if snapshot.name not in time_dimensions:
                    issues.append(
                        ManifestIssue(
                            file,
                            f"{where}.non_additive_dimension.name",
                            f"non-additive dimension {snapshot.name!r} must be a time "
                            f"dimension of semantic model {model.name!r}",
                        )
                    )
                for k, grouping in enumerate(snapshot.window_groupings):
                    if grouping not in entity_names:
                        issues.append(
                            ManifestIssue(
                                file,
                                f"{where}.non_additive_dimension.window_groupings[{k}]",
                                f"unknown entity {grouping!r} in semantic model {model.name!r}",
                            )
                        )

    for entity, holders in entity_holders.items():
        if len({model.name for _, _, model, _ in holders}) > 1 and not any(
            model.entities[j].type in ("primary", "unique") for _, _, model, j in holders
        ):
            file, at, _, j = holders[1]
            models = ", ".join(sorted({model.name for _, _, model, _ in holders}))
            issues.append(
                ManifestIssue(
                    file,
                    f"{at}.entities[{j}]",
                    f"entity {entity!r} is foreign in {models} with no primary or unique "
                    "side: joining them would fan out (many-to-many)",
                )
            )

    metric_seen: dict[str, tuple[str, Metric]] = {}
    for file, document in parsed:
        for k, metric in enumerate(document.metrics):
            if metric.name in metric_seen:
                issues.append(
                    ManifestIssue(
                        file,
                        f"metrics[{k}].name",
                        f"metric {metric.name!r} is defined more than once "
                        f"(first in {metric_seen[metric.name][0]})",
                    )
                )
            metric_seen.setdefault(metric.name, (file, metric))

    for file, document in parsed:
        for k, metric in enumerate(document.metrics):
            if metric.measure is not None and metric.measure not in measure_seen:
                issues.append(
                    ManifestIssue(
                        file, f"metrics[{k}].measure", f"unknown measure {metric.measure!r}"
                    )
                )
            if metric.replaced_by is not None and metric.replaced_by not in metric_seen:
                issues.append(
                    ManifestIssue(
                        file,
                        f"metrics[{k}].replaced_by",
                        f"unknown metric {metric.replaced_by!r}",
                    )
                )
            for field in ("numerator", "denominator"):
                reference = getattr(metric, field)
                if reference is None:
                    continue
                target = metric_seen.get(reference)
                if target is None:
                    issues.append(
                        ManifestIssue(
                            file, f"metrics[{k}].{field}", f"unknown metric {reference!r}"
                        )
                    )
                elif target[1].type == "ratio":
                    issues.append(
                        ManifestIssue(
                            file,
                            f"metrics[{k}].{field}",
                            f"{field} must be a simple or cumulative metric; "
                            f"{reference!r} is a ratio",
                        )
                    )
    return issues


def assemble(models: dict[str, SemanticModel], metrics: dict[str, Metric]) -> SemanticManifest:
    """Derive the joins and the version, and hand back a manifest. Structural validation has
    already happened; this is the one place a `SemanticManifest` is constructed."""
    return SemanticManifest(
        semantic_models=models,
        metrics=metrics,
        joins=_joins(models.values()),
        version=_version(models.values(), metrics.values()),
    )


def _metric_filter_issues(parsed, models, metrics, joins) -> list[ManifestIssue]:
    """A filter is part of the definition, so it is checked at load time, not per query."""
    issues: list[ManifestIssue] = []
    for file, document in parsed:
        for k, metric in enumerate(document.metrics):
            if not metric.filters:
                continue
            _, owners = metric_sources(models, metrics, metric)
            if not owners:
                continue  # an unknown measure is already reported
            catalog = dimension_catalog(models, joins, owners[0])
            for i, declared in enumerate(metric.filters):
                outcome = resolve(catalog, declared.field)
                path = f"metrics[{k}].filters[{i}].field"
                if outcome is None:
                    issues.append(
                        ManifestIssue(
                            file,
                            path,
                            f"{declared.field!r} is not an authorised cut of {metric.name!r}",
                        )
                    )
                elif isinstance(outcome, list):
                    issues.append(
                        ManifestIssue(
                            file,
                            path,
                            f"{declared.field!r} exists in more than one table reachable from "
                            f"{metric.name!r}; use one of: {', '.join(outcome)}",
                        )
                    )
    return issues


def _joins(models) -> tuple[Join, ...]:
    holders: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for model in models:
        for entity in model.entities:
            holders[entity.name].append((model.name, entity.type))
    joins: set[Join] = set()
    for entity, sides in holders.items():
        unique = [name for name, kind in sides if kind in ("primary", "unique")]
        for name, kind in sides:
            for target in unique:
                if target == name:
                    continue
                cardinality = "many_to_one" if kind == "foreign" else "one_to_one"
                joins.add(Join(name, target, entity, cardinality))
    return tuple(sorted(joins, key=lambda j: (j.from_model, j.to_model, j.entity)))


def _version(models, metrics) -> str:
    canonical = {
        "semantic_models": [
            m.model_dump(mode="json") for m in sorted(models, key=lambda m: m.name)
        ],
        "metrics": [m.model_dump(mode="json") for m in sorted(metrics, key=lambda m: m.name)],
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
