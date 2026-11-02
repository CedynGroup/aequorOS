"""The authority attestation reads filed bytes under.

Signing and verification both re-read a pinned artifact version, and the storage
kernel requires the authority itself rather than a boolean. One helper keeps the
two paths on the package's own return family instead of a role or a claim.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import AuthorizationDecision, ExaminerDocumentAuthority
from app.core.return_family_access import gate_for
from app.identity import public as authorization_service


def artifact_view_decision(
    db: Session, ctx: TenantContext, *, bank_id: str, return_family: str, surface: str
) -> AuthorizationDecision | ExaminerDocumentAuthority:
    """A fresh whole-institution VIEW decision on the package's exact family gate.

    The bank and the family are passed as values, not as a package row: this
    helper lives in the attestation feature and must not depend on the reporting
    feature's model to describe which gate it reads.
    """
    gate = gate_for(return_family)
    return authorization_service.document_view_decision(
        db,
        ctx,
        bank_id=bank_id,
        module=gate.module,
        sensitivity=gate.sensitivity,
        surface=surface,
    )
