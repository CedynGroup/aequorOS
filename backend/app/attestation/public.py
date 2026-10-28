"""Attestation's interface for other features: re-exports only, no logic."""

from app.attestation.models import AttestationSignature, RegulatoryArtifactVersion
from app.attestation.schemas import SigningRole
from app.attestation.service import (
    digests,
    identity,
    layouts,
    pdf_signing,
    policy,
    register_state,
    workflow,
)
from app.attestation.service.digests import (
    canonical_json,
    digest_of,
    register_state_digest,
    sha256_hex,
)
from app.attestation.service.policy import SigningPolicy
from app.attestation.service.workflow import (
    ensure_register_current,
    ensure_submittable,
    package_policy,
)

__all__ = [
    "AttestationSignature",
    "RegulatoryArtifactVersion",
    "SigningPolicy",
    "SigningRole",
    "canonical_json",
    "digest_of",
    "digests",
    "ensure_register_current",
    "ensure_submittable",
    "identity",
    "layouts",
    "package_policy",
    "pdf_signing",
    "policy",
    "register_state",
    "register_state_digest",
    "sha256_hex",
    "workflow",
]
