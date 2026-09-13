"""What an agent sends: a structured request, never SQL.

Field *values* are deliberately loose — an unknown grain or operator must come back as a
MetricBridge refusal carrying alternatives, not as a schema error the agent cannot act on.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateRangeInput(_Strict):
    start_date: str
    end_date: str


class FilterInput(_Strict):
    field: str
    operator: str
    value: Any = None


class OrderByInput(_Strict):
    field: str
    direction: Literal["asc", "desc"] = "desc"


class QueryRequest(_Strict):
    metric: str
    date_range: DateRangeInput | None = None
    dimensions: list[str] = []
    time_grain: str | None = None
    filters: list[FilterInput] = []
    order_by: list[OrderByInput] = []
    row_limit: int = DEFAULT_ROW_LIMIT
