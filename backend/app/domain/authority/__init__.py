"""Calculation authority primitives (WS-A).

Three shared architectural primitives live here, and nothing else:

* :mod:`app.domain.authority.outcomes` (P3) — the fail-closed result vocabulary
  every material calculation returns instead of inventing a number.
* :mod:`app.domain.authority.registry` (P1) — the machine-readable declaration
  of which engine owns which metric under which regime and methodology.
* :mod:`app.domain.authority.provenance` (P4/P5) — a formal, dependency-free
  interface over the EXISTING ``RegulatoryRun`` provenance. Not a second store.

The package is deliberately pure: no SQLAlchemy, no FastAPI, no service
imports. Anything here must be importable from a pure engine and from a test
with no database.
"""

from __future__ import annotations

from app.domain.authority.outcomes import (
    BLOCKING_STATES,
    CalculationOutcome,
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    Severity,
    outcome,
)
from app.domain.authority.provenance import (
    REQUIRED_PROVENANCE_FIELDS,
    CalculationProvenance,
    ProvenanceIncomplete,
    RunLike,
    parameter_digest,
)
from app.domain.authority.registry import (
    CLASS_SPECIFIC_REGIMES,
    EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
    REGISTRY,
    REQUIRED_AUTHORITY_FIELDS,
    AdvisoryDesignation,
    AuthorityKey,
    CodeEvidence,
    CompletenessFailure,
    DuplicateAuthorityError,
    InstitutionClass,
    MethodologyDivergence,
    MetricAuthority,
    MetricAuthorityRegistry,
    MetricFamily,
    Regime,
    UnknownMetricError,
    all_authorities,
    authorities_for_metric,
    check_completeness,
    get_authority,
)

__all__ = [
    "BLOCKING_STATES",
    "CLASS_SPECIFIC_REGIMES",
    "EXTERNAL_REGULATORY_VERIFICATION_REQUIRED",
    "REGISTRY",
    "REQUIRED_AUTHORITY_FIELDS",
    "REQUIRED_PROVENANCE_FIELDS",
    "AdvisoryDesignation",
    "AuthorityKey",
    "CalculationOutcome",
    "CalculationProvenance",
    "CodeEvidence",
    "CompletenessFailure",
    "DuplicateAuthorityError",
    "InstitutionClass",
    "MethodologyDivergence",
    "MetricAuthority",
    "MetricAuthorityRegistry",
    "MetricFamily",
    "NotComputable",
    "OutcomeDetail",
    "OutcomeState",
    "ProvenanceIncomplete",
    "Regime",
    "RunLike",
    "Severity",
    "UnknownMetricError",
    "all_authorities",
    "authorities_for_metric",
    "check_completeness",
    "get_authority",
    "outcome",
    "parameter_digest",
]
