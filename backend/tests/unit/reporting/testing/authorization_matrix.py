"""Independent HTTP oracle: the examiner evidence operation matrix (issue 180).

The oracle restates the contract instead of calling the production evaluator, so
a generated binding set is checked against a SECOND statement of the same rules.
It reads only the bundle vocabulary and the operation catalogue below.

Two sentences, never composed from parts of each other:

* ENTRY — every examiner route requires AUDIT/restricted ``view`` for the
  institution, from one complete active binding;
* SOURCE — a by-id run read additionally requires the run module's own
  ``confidential`` ``view`` and answers an opaque 404 without it. The run list
  and the documentation package FILTER the same sources out instead, so their
  status never depends on the source sentence.

Route contract: ``backend/docs/examiner_evidence_enforcement_rollout.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from tests.unit.reporting.testing.generators import EvidenceBinding

#: Human bundles that carry ``view``; ``core/authorization.ROLE_PERMISSIONS``.
#: Stated here rather than imported so the oracle is a second opinion, and
#: restricted to human bundles because a human principal cannot hold a machine
#: one (the service and the database both refuse it).
VIEW_BUNDLES = frozenset(
    {
        RoleBundle.VIEWER,
        RoleBundle.AUDITOR,
        RoleBundle.SECURITY_OFFICER,
        RoleBundle.COMPLIANCE_OFFICER,
        RoleBundle.ANALYST,
        RoleBundle.APPROVER,
        RoleBundle.VALIDATOR,
    }
)


@dataclass(frozen=True)
class ExaminerOperation:
    method: str
    path: str
    #: The stored run module the by-id route reads; ``None`` for the two
    #: filtering routes.
    run_module: str | None = None
    #: The source sentence that by-id route additionally needs.
    source_module: ModuleScope | None = None
    source_sensitivity: SensitivityScope | None = None


OPERATIONS: tuple[ExaminerOperation, ...] = (
    ExaminerOperation("GET", "/banks/{bank_id}/examiner/runs"),
    ExaminerOperation("GET", "/banks/{bank_id}/examiner/documentation-package"),
    ExaminerOperation(
        "GET",
        "/banks/{bank_id}/examiner/runs/{run_id}/reproduction",
        run_module="liquidity",
        source_module=ModuleScope.LIQUIDITY,
        source_sensitivity=SensitivityScope.CONFIDENTIAL,
    ),
)


def _matches(
    binding: EvidenceBinding,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    institution_id: str,
) -> bool:
    """One binding satisfies every dimension, or it contributes nothing."""

    if binding.lifecycle != "active" or binding.bundle not in VIEW_BUNDLES:
        return False
    if binding.module not in (module, ModuleScope.ALL):
        return False
    if binding.sensitivity not in (sensitivity, SensitivityScope.ALL):
        return False
    # An organization-wide row covers every institution of the organization; a
    # narrowed row covers exactly the institution it names.
    return binding.institution_id in (None, institution_id)


def permits(
    bindings: list[EvidenceBinding],
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    institution_id: str,
) -> bool:
    """Whether any ONE binding is a complete match for the sentence."""

    return any(
        _matches(binding, module=module, sensitivity=sensitivity, institution_id=institution_id)
        for binding in bindings
    )


def entry_permitted(bindings: list[EvidenceBinding], *, institution_id: str) -> bool:
    """The AUDIT/restricted ``view`` sentence every examiner route shares."""

    return permits(
        bindings,
        module=ModuleScope.AUDIT,
        sensitivity=SensitivityScope.RESTRICTED,
        institution_id=institution_id,
    )


def expected_status(
    bindings: list[EvidenceBinding],
    operation: ExaminerOperation,
    *,
    institution_id: str,
) -> int:
    """The one status the oracle admits for this binding set on this operation."""

    if not entry_permitted(bindings, institution_id=institution_id):
        return 403
    if operation.source_module is None or operation.source_sensitivity is None:
        return 200
    if not permits(
        bindings,
        module=operation.source_module,
        sensitivity=operation.source_sensitivity,
        institution_id=institution_id,
    ):
        return 404
    return 200
