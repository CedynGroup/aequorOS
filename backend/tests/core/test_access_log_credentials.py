from __future__ import annotations

import json
import logging
from typing import cast

import pytest
from loguru import logger

from app.core.logging import configure_logging


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/storage/download?token=synthetic-secret&other=value",
        "/documents/file?X-Amz-Signature=synthetic-secret",
        "/api/v1/storage/download?%74oken=synthetic-secret",
        "/health",
    ],
)
def test_access_logging_keeps_request_details_without_query_credentials(
    path: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.root, "handlers", list(logging.root.handlers))
    monkeypatch.setattr(logging.root, "level", logging.root.level)
    try:
        configure_logging("INFO")
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1234", "GET", path, "1.1", 200
        )
        output = capsys.readouterr().err
        assert "synthetic-secret" not in output
        record = cast(dict[str, object], json.loads(output))["record"]
        assert isinstance(record, dict)
        assert record["message"] == f'127.0.0.1:1234 - "GET {path.partition("?")[0]} HTTP/1.1" 200'
    finally:
        logger.remove()
        logger.configure(patcher=None)


def test_unstructured_access_record_does_not_expose_credentials(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.root, "handlers", list(logging.root.handlers))
    monkeypatch.setattr(logging.root, "level", logging.root.level)
    try:
        configure_logging("INFO")
        logging.getLogger("uvicorn.access").info("GET /download?token=synthetic-secret")
        output = capsys.readouterr().err
        assert "synthetic-secret" not in output
        record = cast(dict[str, object], json.loads(output))["record"]
        assert isinstance(record, dict) and record["message"] == "HTTP request"
    finally:
        logger.remove()
        logger.configure(patcher=None)
