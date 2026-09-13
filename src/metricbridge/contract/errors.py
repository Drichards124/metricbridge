"""Structured refusals.

An agent that reads `error: invalid dimension` guesses again. An agent that reads the code, the
offending value and the authorised alternatives repairs itself on the next turn. So a refusal is
data, not prose — and the codes are public API: they are added, never silently repurposed.
"""

from typing import Any

from pydantic import BaseModel


class Refusal(BaseModel):
    """One machine-readable reason a request was refused."""

    code: str
    message: str
    field: str | None = None
    offending_value: Any | None = None
    remediation: str | None = None
    valid_alternatives: list[str] = []

    def as_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class RefusalError(Exception):
    """Raised when a request cannot be compiled. Carries every refusal, never just the first:
    an agent that repairs one problem per round trip spends its context on ceremony."""

    def __init__(self, refusals: list[Refusal]) -> None:
        self.refusals = refusals
        super().__init__(f"{len(refusals)} refusal(s): " + "; ".join(r.message for r in refusals))

    def payload(self) -> dict:
        return {"ok": False, "errors": [r.as_dict() for r in self.refusals]}
