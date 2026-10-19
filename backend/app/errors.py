from typing import Any


class ApiError(Exception):
    """Domain error rendered as ``{"error": {"code", "message", ...extra}}``."""

    def __init__(self, status_code: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def not_found(what: str = "Work item") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")
