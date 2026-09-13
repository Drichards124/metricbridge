"""The semantic manifest model: what a table offers, and which metrics may be asked of it.

Shaped after dbt MetricFlow's semantic interfaces — semantic models carrying entities, dimensions
and measures, and metrics that reference measures — so a dbt adapter maps onto it rather than
translating. Phase 1 admits a subset; everything outside it is refused by name, never ignored.

Unknown keys are refused too: a misspelt `aggr:` must fail loudly, not load as a metric with a
silently missing aggregation.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

TimeGranularity = Literal["day", "week", "month", "quarter", "year"]
Aggregation = Literal["sum", "count", "count_distinct", "min", "max", "average"]
EntityType = Literal["primary", "unique", "foreign"]
MetricType = Literal["simple", "ratio", "cumulative"]
Tier = Literal["certified", "experimental", "deprecated"]

GRANULARITIES = ("day", "week", "month", "quarter", "year")
AGGREGATIONS = ("sum", "count", "count_distinct", "min", "max", "average")
ENTITY_TYPES = ("primary", "unique", "foreign")
METRIC_TYPES = ("simple", "ratio", "cumulative")

# Valid in MetricFlow, deferred here. Named so the refusal says "not yet", not "never heard of it".
DEFERRED_GRANULARITIES = ("nanosecond", "microsecond", "millisecond", "second", "minute", "hour")
DEFERRED_AGGREGATIONS = ("percentile", "median", "sum_boolean")
DEFERRED_ENTITY_TYPES = ("natural",)
DEFERRED_METRIC_TYPES = ("derived", "conversion")

_WINDOW = re.compile(r"^\s*(\d+)\s+(day|week|month|quarter|year)s?\s*$")


def _choice(
    value: object, supported: tuple[str, ...], deferred: tuple[str, ...], noun: str
) -> object:
    if value in supported:
        return value
    if value in deferred:
        raise ValueError(f"{noun} {value!r} is not supported in this version")
    raise ValueError(f"unknown {noun} {value!r}; expected one of: {', '.join(supported)}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Expressed(_Strict):
    """An element backed by a warehouse expression, which defaults to the element's own name."""

    @model_validator(mode="before")
    @classmethod
    def expr_defaults_to_name(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("expr") is None and "name" in data:
            return {**data, "expr": data["name"]}
        return data


class Entity(_Expressed):
    """A join key. Its type decides join cardinality: foreign → primary/unique is many-to-one."""

    name: str
    type: EntityType
    expr: str
    description: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def supported_type(cls, value: object) -> object:
        return _choice(value, ENTITY_TYPES, DEFERRED_ENTITY_TYPES, "entity type")


class Dimension(_Expressed):
    """A cut. A time dimension marked `is_partition` is what every query's date bound prunes on."""

    name: str
    type: Literal["categorical", "time"]
    expr: str
    time_granularity: TimeGranularity | None = Field(default=None, validate_default=True)
    is_partition: bool = False
    description: str = ""

    @field_validator("time_granularity", mode="before")
    @classmethod
    def supported_granularity(cls, value: object) -> object:
        if value is None:
            return None
        return _choice(value, GRANULARITIES, DEFERRED_GRANULARITIES, "time granularity")

    @field_validator("time_granularity")
    @classmethod
    def granularity_matches_type(cls, value: str | None, info: ValidationInfo) -> str | None:
        kind = info.data.get("type")
        if kind == "time" and value is None:
            raise ValueError("time_granularity is required for time dimensions")
        if kind == "categorical" and value is not None:
            raise ValueError("time_granularity is only allowed on time dimensions")
        return value

    @field_validator("is_partition")
    @classmethod
    def partition_is_time(cls, value: bool, info: ValidationInfo) -> bool:
        if value and info.data.get("type") != "time":
            raise ValueError("only time dimensions can be partition dimensions")
        return value


class NonAdditiveDimension(_Strict):
    """How a snapshot measure rolls up: take the value at the min or max of this time dimension
    within each group, instead of summing across it."""

    name: str
    window_choice: Literal["min", "max"]
    window_groupings: list[str] = Field(default_factory=list)


class Measure(_Expressed):
    """A measure is additive unless declared otherwise. A non-additive measure is a snapshot:
    summing it across time counts the same stock once per period. Declaring
    `non_additive_dimension` says how to roll it up; leaving it out means such a request is
    refused rather than answered with a plausible wrong number."""

    name: str
    agg: Aggregation
    expr: str
    description: str = ""
    additive: bool = True
    non_additive_dimension: NonAdditiveDimension | None = Field(default=None, validate_default=True)

    @field_validator("non_additive_dimension")
    @classmethod
    def rollup_needs_a_non_additive_measure(
        cls, value: NonAdditiveDimension | None, info: ValidationInfo
    ) -> NonAdditiveDimension | None:
        if value is not None and info.data.get("additive") is not False:
            raise ValueError("a measure declaring non_additive_dimension must set additive: false")
        return value

    @field_validator("agg", mode="before")
    @classmethod
    def supported_aggregation(cls, value: object) -> object:
        return _choice(value, AGGREGATIONS, DEFERRED_AGGREGATIONS, "aggregation")


class SemanticModel(_Strict):
    """One warehouse table and everything that can honestly be asked of it."""

    name: str
    table: str
    description: str = ""
    entities: list[Entity] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)

    @property
    def partition(self) -> Dimension | None:
        return next((d for d in self.dimensions if d.is_partition), None)


class MetricWindow(_Strict):
    count: int = Field(gt=0)
    granularity: TimeGranularity


_ALLOWED_PARAMS = {
    "simple": {"measure"},
    "ratio": {"numerator", "denominator"},
    "cumulative": {"measure", "window", "grain_to_date"},
}
_REQUIRED_PARAMS = {
    "simple": {"measure"},
    "ratio": {"numerator", "denominator"},
    "cumulative": {"measure"},
}


class Metric(_Strict):
    """A governed metric. Absent from the manifest means unqueryable: the catalog is a whitelist."""

    name: str
    type: MetricType
    description: str
    tier: Tier = "experimental"
    owner: str | None = None
    replaced_by: str | None = Field(default=None, validate_default=True)
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("replaced_by")
    @classmethod
    def only_deprecated_metrics_have_a_successor(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is not None and info.data.get("tier") != "deprecated":
            raise ValueError("replaced_by is only allowed on a deprecated metric")
        return value

    max_window_days: int | None = Field(default=None, gt=0)
    measure: str | None = Field(default=None, validate_default=True)
    numerator: str | None = Field(default=None, validate_default=True)
    denominator: str | None = Field(default=None, validate_default=True)
    window: MetricWindow | None = Field(default=None, validate_default=True)
    grain_to_date: TimeGranularity | None = Field(default=None, validate_default=True)

    @field_validator("type", mode="before")
    @classmethod
    def supported_type(cls, value: object) -> object:
        return _choice(value, METRIC_TYPES, DEFERRED_METRIC_TYPES, "metric type")

    @field_validator("window", mode="before")
    @classmethod
    def parse_window(cls, value: object) -> object:
        if value is None or isinstance(value, dict):
            return value
        match = _WINDOW.match(str(value))
        if match is None:
            raise ValueError(
                f"invalid window {value!r}; expected '<count> <granularity>', e.g. '12 months'"
            )
        return {"count": int(match.group(1)), "granularity": match.group(2)}

    @field_validator("grain_to_date", mode="before")
    @classmethod
    def supported_grain_to_date(cls, value: object) -> object:
        if value is None:
            return None
        return _choice(value, GRANULARITIES, DEFERRED_GRANULARITIES, "time granularity")

    @field_validator("measure", "numerator", "denominator", "window", "grain_to_date")
    @classmethod
    def params_match_type(cls, value: object, info: ValidationInfo) -> object:
        kind = info.data.get("type")
        if kind is None:  # the type itself was refused and already reported
            return value
        if value is not None and info.field_name not in _ALLOWED_PARAMS[kind]:
            raise ValueError(f"{info.field_name} is not allowed for {kind} metrics")
        if value is None and info.field_name in _REQUIRED_PARAMS[kind]:
            raise ValueError(f"{info.field_name} is required for {kind} metrics")
        return value

    @model_validator(mode="after")
    def cumulative_has_one_window(self) -> "Metric":
        if self.type == "cumulative" and (self.window is None) == (self.grain_to_date is None):
            raise ValueError("cumulative metrics need exactly one of window or grain_to_date")
        return self


class ManifestFile(_Strict):
    semantic_models: list[SemanticModel] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
