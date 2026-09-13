"""Feature census over public semantic manifests — a reality check on our model.

We have no private dbt project to test against, so we ask a public one what real semantic layers
actually declare. MetricFlow's fixture manifests are written in dbt's own YAML shape, so loading
them with our loader would measure format translation, not modelling coverage. Instead this counts
the *features* they use and maps each to our status, which is the question that matters: can the
MetricBridge model express what people really model?

Feature extraction is pure and unit-tested offline; only `fetch` touches the network.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SOURCE_REPO = "https://github.com/dbt-labs/metricflow.git"
SOURCE_COMMIT = "e2f17cbb6563f1ed90450639e51588057da0ba4e"  # pinned: a census must be reproducible
SOURCE_PATH = "metricflow_semantics/test_helpers/semantic_manifest_yamls"

SUPPORTED = "supported"
DEFERRED = "deferred"
GAP = "gap"

# Every feature we count, and where MetricBridge stands on it today.
STATUS: dict[str, tuple[str, str]] = {
    "metric:simple": (SUPPORTED, ""),
    "metric:ratio": (SUPPORTED, ""),
    "metric:cumulative": (SUPPORTED, ""),
    "metric:derived": (DEFERRED, "D9 — Phase 2"),
    "metric:conversion": (DEFERRED, "D9 — Phase 2"),
    "agg:sum": (SUPPORTED, ""),
    "agg:count": (SUPPORTED, ""),
    "agg:count_distinct": (SUPPORTED, "roll-up recomputed from base rows in 1.4 (D11)"),
    "agg:min": (SUPPORTED, ""),
    "agg:max": (SUPPORTED, ""),
    "agg:average": (SUPPORTED, "roll-up recomputed from base rows in 1.4 (D11)"),
    "agg:sum_boolean": (DEFERRED, "D9 — Phase 2"),
    "agg:median": (DEFERRED, "D9 — Phase 2"),
    "agg:percentile": (DEFERRED, "D9 — Phase 2"),
    "entity:primary": (SUPPORTED, ""),
    "entity:unique": (SUPPORTED, ""),
    "entity:foreign": (SUPPORTED, ""),
    "entity:natural": (DEFERRED, "SCD type 2 — Phase 2 (D11)"),
    "dimension:categorical": (SUPPORTED, ""),
    "dimension:time": (SUPPORTED, ""),
    "dimension:validity_params": (DEFERRED, "SCD validity windows — Phase 2 (D11)"),
    "dimension:sub_day_granularity": (DEFERRED, "D9 — Phase 2"),
    "dimension:is_partition": (SUPPORTED, ""),
    "measure:non_additive_dimension": (SUPPORTED, "declared roll-up (D8)"),
    "measure:agg_time_dimension": (GAP, "aggregation time vs partition column — 1.4 (D11)"),
    "model:defaults_agg_time_dimension": (GAP, "same as above — 1.4 (D11)"),
    "model:primary_entity": (SUPPORTED, ""),
    "metric:filter": (GAP, "planned: milestone 1.2c (D14)"),
    "metric:offset_window": (GAP, "period-over-period offsets — Phase 2"),
    "metric:offset_to_grain": (GAP, "period-over-period offsets — Phase 2"),
    "metric:fill_nulls_with": (GAP, "gap filling — Phase 2"),
    "metric:join_to_timespine": (GAP, "time-spine joins — Phase 2"),
    "join:multi_hop": (DEFERRED, "single-hop only in v0 (declined #5)"),
}


@dataclass
class Census:
    features: Counter = field(default_factory=Counter)
    projects: dict[str, Counter] = field(default_factory=dict)
    semantic_models: int = 0
    metrics: int = 0
    files: int = 0

    def note(self, project: str, feature: str) -> None:
        self.features[feature] += 1
        self.projects.setdefault(project, Counter())[feature] += 1


def _documents(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def _fold(value: object) -> str:
    return value.lower() if isinstance(value, str) else str(value)


def _count_semantic_model(census: Census, project: str, model: dict) -> None:
    census.semantic_models += 1
    if model.get("primary_entity"):
        census.note(project, "model:primary_entity")
    if (model.get("defaults") or {}).get("agg_time_dimension"):
        census.note(project, "model:defaults_agg_time_dimension")
    for entity in model.get("entities") or []:
        census.note(project, f"entity:{_fold(entity.get('type'))}")
    for dimension in model.get("dimensions") or []:
        census.note(project, f"dimension:{_fold(dimension.get('type'))}")
        params = dimension.get("type_params") or {}
        if dimension.get("is_partition"):
            census.note(project, "dimension:is_partition")
        if params.get("validity_params"):
            census.note(project, "dimension:validity_params")
        grain = params.get("time_granularity")
        if grain in ("nanosecond", "microsecond", "millisecond", "second", "minute", "hour"):
            census.note(project, "dimension:sub_day_granularity")
    for measure in model.get("measures") or []:
        census.note(project, f"agg:{_fold(measure.get('agg'))}")
        if measure.get("non_additive_dimension"):
            census.note(project, "measure:non_additive_dimension")
        if measure.get("agg_time_dimension"):
            census.note(project, "measure:agg_time_dimension")


def _count_metric(census: Census, project: str, metric: dict) -> None:
    census.metrics += 1
    census.note(project, f"metric:{_fold(metric.get('type'))}")
    if metric.get("filter"):
        census.note(project, "metric:filter")
    params = metric.get("type_params") or {}
    for key in ("offset_window", "offset_to_grain", "fill_nulls_with", "join_to_timespine"):
        if params.get(key):
            census.note(project, f"metric:{key}")
    for inner in params.get("metrics") or []:
        if isinstance(inner, dict):
            for key in ("offset_window", "offset_to_grain"):
                if inner.get(key):
                    census.note(project, f"metric:{key}")


def _has_multi_hop(models: list[dict]) -> bool:
    """True when a join path of two or more hops exists: A --foreign--> B --foreign--> C."""
    unique_side: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for model in models:
        name = model.get("name", "")
        for entity in model.get("entities") or []:
            kind = _fold(entity.get("type"))
            if kind in ("primary", "unique", "natural"):
                unique_side.setdefault(entity.get("name", ""), set()).add(name)
            elif kind == "foreign":
                outgoing.setdefault(name, set()).add(entity.get("name", ""))
    reachable = {
        model: {t for e in entities for t in unique_side.get(e, set()) if t != model}
        for model, entities in outgoing.items()
    }
    return any(
        any(hop != model and (reachable.get(hop) or set()) - {model, hop} for hop in hops)
        for model, hops in reachable.items()
    )


def collect_features(root: Path) -> Census:
    """Walk a tree of dbt-shaped semantic manifests and count what they declare."""
    census = Census()
    models_by_project: dict[str, list[dict]] = {}
    for path in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        project = path.relative_to(root).parts[0]
        census.files += 1
        for document in _documents(path):
            found = ([document["semantic_model"]] if "semantic_model" in document else []) + (
                document.get("semantic_models") or []
            )
            for model in found:
                models_by_project.setdefault(project, []).append(model)
                _count_semantic_model(census, project, model)
            if "metric" in document:
                _count_metric(census, project, document["metric"])
            for metric in document.get("metrics") or []:
                _count_metric(census, project, metric)
    for project, models in models_by_project.items():
        if _has_multi_hop(models):
            census.note(project, "join:multi_hop")
    return census


def report(census: Census) -> str:
    unknown = sorted(f for f in census.features if f not in STATUS)
    gaps = sorted(f for f in census.features if STATUS.get(f, (GAP, ""))[0] == GAP)
    lines = [
        "# Manifest coverage",
        "",
        "A feature census of public semantic manifests, asking whether the MetricBridge model can",
        "express what real semantic layers declare. Source: dbt-labs/metricflow fixture manifests",
        f"(Apache-2.0) at `{SOURCE_COMMIT[:12]}`, fetched and never vendored. Regenerate with",
        "`make census`.",
        "",
        (
            f"**{census.files} files · {census.semantic_models} semantic models"
            f" · {census.metrics} metrics · {len(census.projects)} manifests**"
        ),
        "",
        "| Feature | Uses | Status | Note |",
        "| --- | --- | --- | --- |",
    ]
    for feature, count in sorted(census.features.items(), key=lambda kv: (-kv[1], kv[0])):
        status, note = STATUS.get(feature, (GAP, "**not counted before — review this**"))
        lines.append(f"| `{feature}` | {count} | {status} | {note} |")
    lines += ["", "## Gaps to answer", ""]
    outstanding = sorted(set(gaps) | set(unknown))
    if outstanding:
        lines += [
            f"- `{feature}` — {STATUS.get(feature, (GAP, 'unrecognised feature'))[1]}"
            for feature in outstanding
        ]
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def fetch(destination: Path) -> Path:
    """Sparse shallow clone of the pinned commit — one network operation, no vendored copy."""
    if not (destination / ".git").exists():
        destination.mkdir(parents=True, exist_ok=True)
        run = lambda *args: subprocess.run(args, cwd=destination, check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "remote", "add", "origin", SOURCE_REPO)
        run("git", "sparse-checkout", "init", "--cone")
        run("git", "sparse-checkout", "set", SOURCE_PATH)
        run("git", "fetch", "-q", "--depth", "1", "origin", SOURCE_COMMIT)
        run("git", "checkout", "-q", "FETCH_HEAD")
    return destination / SOURCE_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(".census-cache"))
    parser.add_argument("--out", type=Path, default=Path("docs/conformance/manifest-coverage.md"))
    parser.add_argument("--source", type=Path, help="skip fetching; census this directory instead")
    args = parser.parse_args()

    root = args.source or fetch(args.cache)
    census = collect_features(root)
    if not census.files:
        print(f"no manifest files found under {root}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report(census))
    print(
        f"{census.files} files · {census.semantic_models} semantic models"
        f" · {census.metrics} metrics -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
