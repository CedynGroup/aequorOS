"""Request-local disclosure state for protected presentation reads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, cast
from uuid import UUID

from starlette.types import ASGIApp, Message, Receive, Scope, Send

type LockReason = Literal["available", "revoked", "unavailable", "support"]
HEADER = "X-Aequoros-Protected-Data"
SESSION_STATE = "protected_data_presentation"
SESSION_SUPPORT = "protected_data_support"
LOCKED_TEXT = "🔒 Protected data"
LOCKED_JSON = "_aequoros_protected_data"


@dataclass
class PresentationState:
    support: bool = False
    banks: dict[str, LockReason] = field(default_factory=dict)
    observed_banks: set[str] = field(default_factory=set)
    read_locks: dict[UUID, Literal["revoked", "unavailable"]] = field(default_factory=dict)

    def record(self, bank: str, reason: LockReason) -> None:
        severity = ("available", "unavailable", "revoked", "support")
        if severity.index(reason) >= severity.index(self.banks.get(bank, "available")):
            self.banks[bank] = reason


def contains_placeholder(value: object) -> bool:
    if isinstance(value, str):
        return value == LOCKED_TEXT
    if isinstance(value, dict):
        return LOCKED_JSON in value or any(
            contains_placeholder(item) for item in cast(dict[str, object], value).values()
        )
    if isinstance(value, list):
        return any(contains_placeholder(item) for item in cast(list[object], value))
    return False


class ProtectedDataMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = PresentationState()
        cast(dict[str, object], scope.setdefault("state", {}))[SESSION_STATE] = state

        async def send_with_state(message: Message) -> None:
            if message["type"] == "http.response.start" and state.banks:
                headers = list(cast(list[tuple[bytes, bytes]], message.get("headers", [])))
                headers.append((HEADER.lower().encode(), json.dumps(state.banks).encode()))
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_state)
