"""Synthetic governance reasons and workflow prefixes stay within their contracts."""

from hypothesis import given

from app.core.authorization import GrantReasonCategory, ModuleScope, Permission, Sensitivity
from app.identity.schemas.authorization import AccessRequestCreate
from tests.identity.testing.generators import (
    REQUEST_CHECKPOINTS,
    TEMPLATE_CHECKPOINTS,
    engagement_reasons,
    request_checkpoints,
    template_checkpoints,
)


@given(reason=engagement_reasons(), request=request_checkpoints(), template=template_checkpoints())
def test_approval_inputs_are_valid(reason: str, request: str, template: str) -> None:
    payload = AccessRequestCreate(
        route="/fx",
        institution_id="BK-SAMP0001",
        module_scope=ModuleScope.FX,
        sensitivity_scope=Sensitivity.AGGREGATED,
        permission=Permission.VIEW,
        reason_category=GrantReasonCategory.PROJECT_ENGAGEMENT,
        reason_detail=reason,
    )
    assert payload.reason_detail == reason.strip()
    assert request in REQUEST_CHECKPOINTS
    assert template in TEMPLATE_CHECKPOINTS
