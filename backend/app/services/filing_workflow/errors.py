"""The refusal shapes a review chain raises, shared by both planes.

Lifted out of ``services/icaap/guards.py`` so the shared engine can raise them
without importing the ICAAP. ``guards`` re-exports these names, so every
existing ``guards.conflict(...)`` call site is unchanged and there is still one
implementation.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException, status

_NOT_FOUND = "Not Found"


def conflict(error_code: str, message: str, **extra: Any) -> HTTPException:
    """The repo's 409 shape: a machine code, a sentence, and the facts."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": error_code, "message": message, **extra},
    )


def unprocessable(error_code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error_code": error_code, "message": message, **extra},
    )


def not_found() -> NoReturn:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


__all__ = ["conflict", "not_found", "unprocessable"]
