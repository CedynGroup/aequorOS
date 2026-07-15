from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import REQUEST_ID_HEADER, logger, reset_request_id, set_request_id

RequestHandler = Callable[[Request], Awaitable[Response]]


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        token = set_request_id(request_id)
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.bind(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            ).info("Request completed")
            return response
        finally:
            reset_request_id(token)
