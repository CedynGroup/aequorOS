"""Attestation's interface for other features: re-exports only, no logic."""

from app.models.attestation import AttestationSignature, RegulatoryArtifactVersion
from app.schemas.attestation import SigningRole
from app.services.attestation import (
    digests,
    identity,
    layouts,
    pdf_signing,
    policy,
    register_state,
    workflow,
)
from app.services.attestation.digests import (
    canonical_json,
    digest_of,
    register_state_digest,
    sha256_hex,
)
from app.services.attestation.policy import SigningPolicy
from app.services.attestation.workflow import ensure_submittable, package_policy

__all__ = [
    "AttestationSignature",
    "RegulatoryArtifactVersion",
    "SigningPolicy",
    "SigningRole",
    "canonical_json",
    "digest_of",
    "digests",
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
