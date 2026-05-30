from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id, logger
from app.schemas.common import ErrorBody, ErrorResponse

OPENAPI_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ErrorResponse,
        "description": "Bad request.",
    },
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": "Missing or invalid tenant context.",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Requested resource was not found for the tenant.",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Workflow transition or state conflict.",
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "Request validation failed.",
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Unexpected server error.",
    },
}


def build_error_payload(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
) -> dict[str, Any]:
    response = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        ),
    )
    return response.model_dump(exclude_none=True)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        status_code = exc.status_code
        try:
            phrase = HTTPStatus(status_code).phrase
        except ValueError:
            phrase = "Error"
        message = exc.detail if isinstance(exc.detail, str) else phrase

        return JSONResponse(
            status_code=status_code,
            content=build_error_payload(
                code=_code_for_http_status(status_code),
                message=message,
                request_id=get_request_id(),
                details=None if isinstance(exc.detail, str) else jsonable_encoder(exc.detail),
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=build_error_payload(
                code="validation_error",
                message="Request validation failed.",
                request_id=get_request_id(),
                details=jsonable_encoder(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.bind(
            method=request.method,
            path=request.url.path,
        ).opt(exception=exc).error("Unhandled exception while processing request")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=build_error_payload(
                code="internal_server_error",
                message="An unexpected error occurred.",
                request_id=_request_id_from_request(request),
            ),
        )


def _code_for_http_status(status_code: int) -> str:
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        return "service_unavailable"
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "unauthorized"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "forbidden"
    return "http_error"


def _request_id_from_request(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    return get_request_id()
