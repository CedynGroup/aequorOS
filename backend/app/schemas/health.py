from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import AppEnv

ComponentStatus = Literal["ok", "degraded", "failed", "skipped"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    environment: AppEnv
    status: Literal["ok"]


class ComponentHealth(BaseModel):
    """One subsystem's verdict, with the reason it reached it.

    ``detail`` is always populated for anything other than a plain ``ok`` so an
    operator reading a probe response is told what to change, not merely that
    something is wrong.
    """

    model_config = ConfigDict(frozen=True)

    status: ComponentStatus
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Per-subsystem readiness.

    Deliberately NOT a ``HealthResponse`` subclass with subsystems nested under
    ``database``: until 2026-08-21 storage health was reported as
    ``database.storage``, which read as a property of the database and made a
    storage outage look like a database one (audit finding P0-17). Each
    subsystem is now its own entry under ``checks`` so a consumer can tell them
    apart, and ``status`` can say "serving, but something is wrong" without
    either lying or failing the probe.
    """

    model_config = ConfigDict(frozen=True)

    service: str
    environment: AppEnv
    status: Literal["ok", "degraded"]
    checks: dict[str, ComponentHealth]
