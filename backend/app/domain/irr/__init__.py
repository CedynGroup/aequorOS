"""Pure interest-rate-risk-in-the-banking-book (IRRBB) engine package."""

from typing import Any


class IrrRunError(Exception):
    """Domain input failure persisted onto a run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
