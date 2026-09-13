"""The metric contract: what may be asked of a metric, and how a bad request is refused."""

from ..manifest import OPERATORS
from .api import signature, unknown_metric, validate
from .context import DateRange, Resolved, ResolvedDimension, ResolvedFilter
from .errors import Refusal, RefusalError
from .request import (
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    DateRangeInput,
    FilterInput,
    OrderByInput,
    QueryRequest,
)
from .rules import CODES, RULES, Rule

__all__ = [
    "CODES",
    "DEFAULT_ROW_LIMIT",
    "MAX_ROW_LIMIT",
    "OPERATORS",
    "RULES",
    "DateRange",
    "DateRangeInput",
    "FilterInput",
    "OrderByInput",
    "QueryRequest",
    "Refusal",
    "RefusalError",
    "Resolved",
    "ResolvedDimension",
    "ResolvedFilter",
    "Rule",
    "signature",
    "unknown_metric",
    "validate",
]
