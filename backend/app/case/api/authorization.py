"""Operation-specific case dependencies, evaluated before handlers run."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import DbSession, ScopedMutationTenant, Tenant
from app.case.service.authorization import require_permission
from app.core.authorization import Permission, Sensitivity
from app.core.tenancy import TenantContext


def _access(
    permission: Permission, sensitivity: Sensitivity, *, body_reference: bool = False
) -> Callable[..., TenantContext]:
    def opaque(request: Request) -> bool:
        return body_reference or any(
            name.endswith("_id") for name in (*request.path_params, *request.query_params)
        )

    def read(request: Request, db: DbSession, ctx: Tenant) -> TenantContext:
        require_permission(db, ctx, permission, sensitivity, opaque=opaque(request))
        return ctx

    def mutate(request: Request, db: DbSession, ctx: ScopedMutationTenant) -> TenantContext:
        require_permission(db, ctx, permission, sensitivity, opaque=opaque(request))
        return ctx

    return read if permission in (Permission.VIEW, Permission.EXPORT) else mutate


CaseView = Annotated[TenantContext, Depends(_access(Permission.VIEW, Sensitivity.CONFIDENTIAL))]
CaseRestrictedView = Annotated[
    TenantContext, Depends(_access(Permission.VIEW, Sensitivity.RESTRICTED))
]
CaseCreate = Annotated[TenantContext, Depends(_access(Permission.CREATE, Sensitivity.CONFIDENTIAL))]
CaseRestrictedCreate = Annotated[
    TenantContext,
    Depends(_access(Permission.CREATE, Sensitivity.RESTRICTED, body_reference=True)),
]
CaseEdit = Annotated[TenantContext, Depends(_access(Permission.EDIT, Sensitivity.CONFIDENTIAL))]
CaseBulkEdit = Annotated[
    TenantContext, Depends(_access(Permission.EDIT, Sensitivity.CONFIDENTIAL, body_reference=True))
]
CaseRestrictedEdit = Annotated[
    TenantContext, Depends(_access(Permission.EDIT, Sensitivity.RESTRICTED))
]
CaseRestrictedRun = Annotated[
    TenantContext, Depends(_access(Permission.RUN, Sensitivity.RESTRICTED))
]
CaseReview = Annotated[TenantContext, Depends(_access(Permission.REVIEW, Sensitivity.CONFIDENTIAL))]
CaseRestrictedReview = Annotated[
    TenantContext, Depends(_access(Permission.REVIEW, Sensitivity.RESTRICTED))
]
CaseExport = Annotated[TenantContext, Depends(_access(Permission.EXPORT, Sensitivity.RESTRICTED))]
