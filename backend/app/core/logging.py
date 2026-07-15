from __future__ import annotations

import logging
import sys
from contextvars import ContextVar, Token
from types import FrameType

from loguru import logger

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> Token[str]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get()


def configure_logging(log_level: str) -> None:
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(log_level)

    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    logger.remove()
    logger.configure(
        patcher=lambda record: record["extra"].setdefault("request_id", get_request_id()),
    )
    logger.add(
        sys.stdout,
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
