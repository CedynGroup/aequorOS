from __future__ import annotations

import hashlib
import logging
import re
import sys
from contextlib import suppress
from contextvars import ContextVar, Token
from types import FrameType
from typing import TYPE_CHECKING, Final, cast
from uuid import uuid4

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_TENANT_ID: Final = re.compile(r"OR-[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{8}")


def set_request_id(request_id: str) -> Token[str]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get()


def safe_request_id(request_id: str) -> str:
    if request_id == "-":
        return request_id
    return "sha256:" + hashlib.sha256(request_id.encode()).hexdigest()


def safe_tenant_id(tenant_id: object) -> str:
    return (
        tenant_id if isinstance(tenant_id, str) and _TENANT_ID.fullmatch(tenant_id) else "unknown"
    )


def _patch_record(record: Record) -> None:
    extra = cast(dict[str, object], record["extra"])
    request_id = str(extra.get("request_id", get_request_id()))
    if record["exception"] is not None:
        tenant_id = safe_tenant_id(extra.get("tenant_id", extra.get("organization_id")))
        if request_id == "-":
            request_id = str(uuid4())
        # Loguru serializes exception values as well as rendered tracebacks.
        # Scrub the record before either representation reaches any sink.
        record["exception"] = None
        record["message"] = "Unexpected application error"
        extra.clear()
        extra.update(error_code="unexpected_error", tenant_id=tenant_id)
    extra["request_id"] = safe_request_id(request_id)


def _write_stderr(message: str) -> None:
    # A sink error must not cause Loguru to print the active exception chain.
    with suppress(Exception):
        _ = sys.stderr.write(message)
        sys.stderr.flush()


def configure_logging(log_level: str) -> None:
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(log_level)

    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    logger.remove()
    logger.configure(patcher=_patch_record)
    logger.add(
        _write_stderr,
        level=log_level.upper(),
        serialize=True,
        backtrace=False,
        diagnose=False,
    )


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
