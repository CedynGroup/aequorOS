"""Feature routers in mounting order."""

from __future__ import annotations

from fastapi import APIRouter


def feature_routers() -> list[APIRouter]:
    from app.enrolment.router import router as ops_router
    from app.filing.router import router as v1_router

    routers: list[APIRouter] = [v1_router, ops_router]
    try:
        from app.portal.router import router as portal_router
    except ImportError:  # pragma: no cover - during incremental builds
        return routers
    routers.insert(1, portal_router)
    return routers
