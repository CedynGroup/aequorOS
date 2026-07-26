"""The error boundary must stay visible to a browser.

A 500 that reaches a browser WITHOUT CORS headers is reported by the browser as a
CORS violation, and any client that maps a header-less failure to "the server is
unreachable" will then tell an operator the backend is down while it is up. That
happened on 2026-07-25: a missing migration surfaced in the dashboard as
"Could not reach the risk service", and the actual `UndefinedColumn` error was
invisible from the console.

The fix is ordering — the catch-all sits BELOW CORSMiddleware — so these tests
assert the ordering property, not the handler in isolation. Asserting only that a
500 has the right JSON body would have passed before the fix too.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.errors import UnhandledExceptionMiddleware, register_exception_handlers
from app.core.request_id import RequestIdMiddleware

ORIGIN = "http://localhost:3001"


@pytest.fixture
def app_with_boundary() -> FastAPI:
    """A minimal app wired in the same order as `create_app`."""
    app = FastAPI()
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        # Stands in for the real case: a DB column the ORM expects but the schema
        # does not have. Anything unhandled takes the same path.
        raise RuntimeError("column does not exist")

    return app


def test_unhandled_error_reaches_the_browser_with_cors_headers(
    app_with_boundary: FastAPI,
) -> None:
    client = TestClient(app_with_boundary, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    # The property that actually matters: without this header the browser hides
    # the response body and reports a CORS error instead.
    assert response.headers["access-control-allow-origin"] == ORIGIN
    body = response.json()["error"]
    assert body["code"] == "internal_server_error"
    # A request_id nobody can read is not a diagnostic; it must survive to the
    # client alongside the header that lets the client read it.
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_the_internal_error_never_leaks_the_exception_text(
    app_with_boundary: FastAPI,
) -> None:
    client = TestClient(app_with_boundary, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": ORIGIN})

    # Making 500s visible must not make them chatty: the operator gets a request
    # ID to correlate with the server log, not the database's internals.
    assert "column does not exist" not in response.text
    assert response.json()["error"]["message"] == "An unexpected error occurred."


def test_handled_http_errors_still_carry_cors_headers(app_with_boundary: FastAPI) -> None:
    # 404s were never broken (they are raised below ExceptionMiddleware), but the
    # new middleware sits in that path too — so prove it did not swallow them.
    client = TestClient(app_with_boundary)
    response = client.get("/nope", headers={"Origin": ORIGIN})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.json()["error"]["code"] == "not_found"
