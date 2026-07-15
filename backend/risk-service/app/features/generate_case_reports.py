from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status
from fastapi.responses import HTMLResponse

from app.api.deps import DbSession, Tenant
from app.services import reports as reports_service

router = APIRouter(prefix="/cases", tags=["cases"])


def prefers_html(accept: str | None) -> bool:
    if not accept:
        return False
    preferences = [
        (*parse_accept_part(part), index)
        for index, part in enumerate(accept.split(","))
        if part.strip()
    ]
    preferences.sort(key=lambda preference: (-preference[1], preference[2]))
    for media_type, quality, _index in preferences:
        if quality <= 0:
            continue
        normalized_media_type = media_type.lower()
        if normalized_media_type in {"text/html", "text/*"}:
            return True
        if normalized_media_type in {"application/json", "application/*", "*/*"}:
            return False
    return False


def parse_accept_part(part: str) -> tuple[str, float]:
    media_type, *parameters = [segment.strip() for segment in part.split(";")]
    quality = 1.0
    for parameter in parameters:
        normalized_parameter = parameter.lower()
        if not normalized_parameter.startswith("q="):
            continue
        try:
            quality = float(normalized_parameter[2:])
        except ValueError:
            quality = 0.0
    return media_type, quality


@router.get(
    "/{case_id}/report",
    response_model=reports_service.RiskReportPayload,
    responses={
        status.HTTP_200_OK: {
            "content": {"text/html": {"schema": {"type": "string"}}},
            "description": "Risk review report as JSON by default, or HTML with Accept: text/html.",
        }
    },
)
def case_report(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    accept: Annotated[str | None, Header(alias="Accept")] = None,
) -> reports_service.RiskReportPayload | HTMLResponse:
    payload = reports_service.report_payload(db, ctx, case_id)
    if prefers_html(accept):
        return HTMLResponse(content=reports_service.report_html(payload))
    return payload
