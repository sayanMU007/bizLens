from typing import Any


class AnalysisError(Exception):
    """A request the engine cannot answer. Messages are written to be shown to users
    (and, in Phase 4, fed back to the planner so it can repair its request)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}
