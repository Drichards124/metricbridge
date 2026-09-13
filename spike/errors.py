"""Structured semantic errors.

An agent that receives `"error: invalid dimension"` guesses again. An agent that receives the
code, the offending value, and the authorized alternatives *repairs itself* on the next turn
without a human in the loop. So every refusal here is data, not prose: a stable `code`, a
human-readable `message`, and `remediation` naming what to do instead.

This is the difference between a firewall that blocks and a firewall that teaches.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ErrorCode = Literal[
    "unknown_metric",
    "unknown_dimension",
    "unsupported_time_grain",
    "missing_partition_filter",
    "partition_window_too_wide",
    "non_additive_cut",
    "unknown_filter_field",
    "row_limit_exceeded",
    "cost_budget_exceeded",
    "guardrail_violation",
]


class SemanticError(BaseModel):
    """One machine-readable reason a request was refused."""

    code: ErrorCode
    message: str
    field: str | None = None
    offending_value: Any | None = None
    remediation: str | None = None
    valid_alternatives: list[str] = []

    def as_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class SemanticRefusal(Exception):
    """Raised when a request cannot be compiled. Carries every error, not just the first —
    an agent that fixes one problem per round trip burns turns for no reason."""

    def __init__(self, errors: list[SemanticError]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} semantic error(s): {errors[0].message}")

    def payload(self) -> dict:
        return {"ok": False, "errors": [e.as_dict() for e in self.errors]}
