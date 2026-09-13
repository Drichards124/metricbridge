"""The semantic contract: what a metric is, and what may legitimately be asked of it.

Metrics are declarative and versioned in YAML beside the warehouse they describe. Nothing in
this module talks to a database — a manifest is a *claim* about the warehouse, and keeping it
inert is what lets the whole contract layer be tested without one.

**Why a local format rather than dbt's `semantic_manifest.json` in v0.** The adapter seam is
`load_manifest`, and a dbt reader plugs in there. Shipping a half-remembered translation of
someone else's schema would be worse than shipping none: the failure mode is silently wrong
metric definitions, which is exactly what this project exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

TimeGrain = Literal["day", "week", "month", "quarter", "year"]
GRAIN_ORDER: tuple[TimeGrain, ...] = ("day", "week", "month", "quarter", "year")


class Dimension(BaseModel):
    """A cut the warehouse can honestly support for this metric."""

    name: str
    column: str
    kind: Literal["categorical", "time"] = "categorical"
    description: str = ""


class Metric(BaseModel):
    """A governed metric: one definition, one owner, one SQL expression.

    `non_additive_dimensions` is the field that prevents the most expensive class of silent
    error. A snapshot metric (inventory on hand, subscriber count) cannot be summed across
    time — doing so double-counts every period. Declaring it here turns a plausible wrong
    answer into a refusal that names the problem.
    """

    name: str
    description: str
    expression: str
    table: str
    time_dimension: str
    partition_column: str
    time_grains: list[TimeGrain] = Field(default_factory=lambda: list(GRAIN_ORDER))
    dimensions: list[Dimension] = Field(default_factory=list)
    domain: str | None = None
    tier: Literal["certified", "experimental", "deprecated"] = "experimental"
    owner: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    additive: bool = True
    non_additive_dimensions: list[str] = Field(default_factory=list)
    max_window_days: int | None = None

    def dimension(self, name: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.name == name), None)

    @property
    def dimension_names(self) -> list[str]:
        return [d.name for d in self.dimensions]

    def search_text(self) -> str:
        parts = [self.name.replace("_", " "), self.description, *self.synonyms,
                 *(d.name.replace("_", " ") for d in self.dimensions)]
        return " ".join(p for p in parts if p).lower()


class SemanticManifest(BaseModel):
    """Every metric the gateway will admit. Anything absent here cannot be queried at all —
    that is the firewall, and it is deliberately a whitelist."""

    metrics: dict[str, Metric] = Field(default_factory=dict)

    def get(self, name: str) -> Metric | None:
        return self.metrics.get(name)

    def domains(self) -> list[str]:
        return sorted({m.domain for m in self.metrics.values() if m.domain})


def load_manifest(path: str | Path) -> SemanticManifest:
    """Load every `*.yml` / `*.yaml` under a directory, or a single file."""
    path = Path(path)
    files = sorted(p for p in ([path] if path.is_file() else [*path.glob("*.yml"), *path.glob("*.yaml")]))
    if not files:
        raise FileNotFoundError(f"no semantic definitions found at {path}")
    metrics: dict[str, Metric] = {}
    for file in files:
        document = yaml.safe_load(file.read_text()) or {}
        for raw in document.get("metrics", []):
            metric = Metric(**raw)
            if metric.name in metrics:
                # Two definitions of one metric is the exact divergence this project exists to
                # stop. Refusing at load time beats serving whichever won the dict race.
                raise ValueError(f"metric {metric.name!r} defined twice ({file} and earlier)")
            metrics[metric.name] = metric
    return SemanticManifest(metrics=metrics)
