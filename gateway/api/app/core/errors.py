"""RFC 9457 problem details and the gateway error codes of SPEC.md."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

ErrorCode = Literal[
    "invalid_request",
    "unauthenticated",
    "certificate_required",
    "certificate_mismatch",
    "invalid_client_assertion",
    "insufficient_scope",
    "idempotency_key_required",
    "idempotency_conflict",
    "signing_key_unknown",
    "signing_key_revoked",
    "signature_invalid",
    "manifest_mismatch",
    "payload_digest_mismatch",
    "declaration_digest_mismatch",
    "document_invalid",
    "document_digest_mismatch",
    "institution_mismatch",
    "return_unknown",
    "return_version_not_applicable",
    "schema_invalid",
    "duplicate_submission",
    "supersedes_not_current",
    "amendment_reason_required",
    "not_found",
    "forbidden",
    "rate_limited",
    "conflict",
]

_DEFAULTS: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "The request is not valid"),
    "unauthenticated": (401, "Authentication is required"),
    "certificate_required": (401, "A client certificate is required"),
    "certificate_mismatch": (401, "The client certificate does not match"),
    "invalid_client_assertion": (401, "The client assertion is not valid"),
    "insufficient_scope": (403, "The access token lacks the required scope"),
    "idempotency_key_required": (400, "An Idempotency-Key header is required"),
    "idempotency_conflict": (409, "The Idempotency-Key was used with a different request"),
    "signing_key_unknown": (422, "The signing key is not registered"),
    "signing_key_revoked": (422, "The signing key is revoked"),
    "signature_invalid": (422, "The filing signature is not valid"),
    "manifest_mismatch": (422, "The signed manifest does not match the request"),
    "payload_digest_mismatch": (422, "The payload digest does not match"),
    "declaration_digest_mismatch": (422, "The declaration digest does not match"),
    "document_invalid": (422, "A filed document is not valid"),
    "document_digest_mismatch": (422, "A filed document does not match the manifest"),
    "institution_mismatch": (403, "The filing is for a different institution"),
    "return_unknown": (422, "The return is not known"),
    "return_version_not_applicable": (
        422,
        "The return version is not published for this reporting date",
    ),
    "schema_invalid": (422, "The payload does not match the return schema"),
    "duplicate_submission": (409, "A current version already exists; file an amendment"),
    "supersedes_not_current": (409, "The superseded receipt is not the current version"),
    "amendment_reason_required": (422, "An amendment needs a reason"),
    "not_found": (404, "Not found"),
    "forbidden": (403, "Not permitted"),
    "rate_limited": (429, "Too many requests"),
    "conflict": (409, "The request conflicts with the current state"),
}


class Issue(BaseModel):
    rule_id: str
    severity: Literal["error", "warning"]
    pointer: str | None
    message: str


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str | None = None
    instance: str | None = None
    issues: list[Issue] | None = None


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        *,
        status: int | None = None,
        issues: list[Issue] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        default_status, title = _DEFAULTS[code]
        super().__init__(detail or title)
        self.code: ErrorCode = code
        self.status: int = status or default_status
        self.title: str = title
        self.detail: str | None = detail
        self.issues: list[Issue] | None = issues
        self.headers: dict[str, str] = headers or {}


def problem_response(
    request: Request | None,
    code: ErrorCode,
    status: int,
    title: str,
    detail: str | None = None,
    issues: list[Issue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    base = _problem_base(request)
    problem = Problem(
        type=base + code,
        title=title,
        status=status,
        code=code,
        detail=detail,
        instance=request.url.path if request is not None else None,
        issues=issues,
    )
    return JSONResponse(
        problem.model_dump(exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


_STATUS_CODES: dict[int, ErrorCode] = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "invalid_request",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
}


def _status_code(status: int) -> ErrorCode:
    return _STATUS_CODES.get(status, "invalid_request")


def _problem_base(request: Request | None) -> str:
    if request is not None:
        state: object = request.app.state  # pyright: ignore[reportAny]
        base: object = getattr(state, "problem_base", None)
        if isinstance(base, str):
            return base
    return "/errors/"


def _describe(exc: RequestValidationError) -> str:
    """Summarise the first validation errors as ``location: message``."""
    parts: list[str] = []
    errors: Sequence[object] = exc.errors()
    for error in errors[:5]:
        if isinstance(error, dict):
            location: object = error.get("loc", ())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            message: object = error.get("msg", "invalid")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            where = ""
            if isinstance(location, (list, tuple)):
                items: list[object] = list(location)  # pyright: ignore[reportUnknownArgumentType]
                where = ".".join(str(p) for p in items)
            parts.append(f"{where}: {message}")
    return "; ".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    async def api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        return problem_response(
            request, exc.code, exc.status, exc.title, exc.detail, exc.issues, exc.headers
        )

    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        return problem_response(
            request, "invalid_request", 400, _DEFAULTS["invalid_request"][1], _describe(exc)
        )

    async def http_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code = _status_code(exc.status_code)
        detail = exc.detail or None
        return problem_response(
            request, code, exc.status_code, _DEFAULTS[code][1], detail, None, None
        )

    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
