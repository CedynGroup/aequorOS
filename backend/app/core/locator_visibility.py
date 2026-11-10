"""SQL visibility over the typed source locator (#186).

Tenant read paths for notifications and queued jobs filter on the requester's
CURRENT view authority in SQL, before counts and pagination. The match rules
are exactly the evaluator's (``app.core.authorization.evaluate_permission``):
one active grant whose bundle carries the permission must match every locator
dimension, and matching grants combine with OR. This module builds that
predicate from already-loaded grants so a read path pays one grants query per
request, never per row. Its correspondence with the evaluator is pinned by
``tests/db/test_locator_visibility_integration.py``.

A row without a complete locator — a legacy row predating the columns, or a
defined worker/system enqueue — matches no grant clause and is hidden; no
inference or backfill (mission D9). The locator takes no part in enqueue,
claim or execution, so the worker keeps running: only tenant-visible reads
filter.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Column, ColumnElement, and_, false, or_, true
from sqlalchemy.orm import InstrumentedAttribute

from app.core.authorization import (
    ROLE_PERMISSIONS,
    BindingGrant,
    InstitutionScope,
    ModuleScope,
    Permission,
    SensitivityScope,
    grant_is_active,
    principal_bundle_compatible,
)

#: A mapped model attribute or a standalone table column carrying a locator
#: dimension (the correspondence test probes a stand-in table).
ColumnRef = InstrumentedAttribute[str | None] | Column[str | None]


def locator_visibility(  # noqa: PLR0913 - the four locator columns are the contract
    grants: Sequence[BindingGrant],
    permission: Permission,
    *,
    institution_scope: ColumnRef,
    bank_id: ColumnRef,
    module: ColumnRef,
    sensitivity: ColumnRef,
) -> ColumnElement[bool]:
    """A row predicate: some grant carrying ``permission`` matches the locator.

    ``grants`` are the caller's already-loaded bindings (typically from
    ``identity.service.authorization.load_permission_grants``). An
    organization-scope grant covers every row of the tenant, institution- and
    organization-scoped alike; an institution-scope grant covers only that
    institution's rows — the same asymmetry
    ``core.authorization._institution_matches`` applies. ``data_scope`` takes
    no part here, exactly as it takes none in ``evaluate_permission``: it
    narrows BI rows, never authority itself.

    The lifecycle, bundle-compatibility and permission tests are applied HERE,
    not left to the caller: a suspended, revoked, expired or future grant
    contributes nothing, so the predicate is total over raw bindings.
    """
    moment = datetime.now(UTC)
    clauses: list[ColumnElement[bool]] = []
    for grant in grants:
        if not grant_is_active(grant, moment)[0]:
            continue
        if not principal_bundle_compatible(grant.principal_type, grant.role_bundle):
            continue
        if permission not in ROLE_PERMISSIONS[grant.role_bundle]:
            continue
        if grant.institution_scope is InstitutionScope.ORGANIZATION:
            institution: ColumnElement[bool] = true()
        else:
            institution = and_(
                institution_scope == InstitutionScope.INSTITUTION.value,
                bank_id == grant.institution_id,
            )
        module_match: ColumnElement[bool] = (
            true() if grant.module_scope is ModuleScope.ALL else module == grant.module_scope.value
        )
        sensitivity_match: ColumnElement[bool] = (
            true()
            if grant.sensitivity_scope is SensitivityScope.ALL
            else sensitivity == grant.sensitivity_scope.value
        )
        clauses.append(and_(institution, module_match, sensitivity_match))
    # A row without a complete locator matches no grant: it is either a legacy
    # row predating the columns or a worker/system enqueue, and both stay
    # hidden whatever the caller holds (mission D9).
    locator_present = and_(
        institution_scope.is_not(None),
        module.is_not(None),
        sensitivity.is_not(None),
    )
    return and_(locator_present, or_(false(), *clauses))
