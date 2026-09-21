"""Organisation-level AI consent and switches. Org Owner only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import AiSettingsAdminTenant, DbSession
from app.schemas.ai import AiCommentarySettingsRead, AiCommentarySettingsUpdate
from app.schemas.common import ErrorResponse
from app.services.ai import tenant_settings

router = APIRouter(tags=["ai-settings"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


@router.get(
    "/organization/ai-settings",
    response_model=AiCommentarySettingsRead,
    operation_id="getAiCommentarySettings",
    responses=_ERRORS,
)
def get_ai_settings(db: DbSession, ctx: AiSettingsAdminTenant) -> AiCommentarySettingsRead:
    return tenant_settings.get(db, ctx)


@router.put(
    "/organization/ai-settings",
    response_model=AiCommentarySettingsRead,
    operation_id="updateAiCommentarySettings",
    responses=_ERRORS,
)
def update_ai_settings(
    payload: AiCommentarySettingsUpdate, db: DbSession, ctx: AiSettingsAdminTenant
) -> AiCommentarySettingsRead:
    return tenant_settings.update(db, ctx, payload)
