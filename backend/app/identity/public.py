"""Identity's interface for other features: re-exports only, no logic."""

from __future__ import annotations

from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.institution_profile import (
    BankLicense,
    BankNameHistory,
    BankProduct,
    InstitutionProfile,
    Outlet,
    RelatedParty,
    RelatedPartyRole,
    Shareholding,
)
from app.identity.models.integration_key import IntegrationKey
from app.identity.models.sso_connection import SsoConnection
from app.identity.models.user import User
from app.identity.schemas.authorization import SodDecisionRead, SodPolicyFindingRead
from app.identity.schemas.banks import BankRead, BankReference, BankReportingPeriodRead
from app.identity.schemas.institution_profile import (
    InstitutionProfileFullRead,
    InstitutionProfileRead,
    RelatedPartyRead,
)
from app.identity.service.auth_throttle import (
    burn_password_check,
    clear_failures,
    lock_expiry,
    minutes_remaining,
    record_failure,
    record_failure_for,
    record_success,
)
from app.identity.service.authorization import (
    EffectiveDataScope,
    binding_is_effective,
    effective_data_scope,
    evaluate_permission,
    evaluate_prefetched_permission,
    institution_grain_decision,
    load_effective_grants,
    prefetch_principal_bindings,
    principal_locator,
    record_binding_decision,
    record_binding_evaluation_failure,
    reduce_data_scope,
    request_wide_condition_checks,
)
from app.identity.service.banks import get_bank_or_404, resolve_bank_reference
from app.identity.service.grant_administration import (
    SodDecision,
    SodFinding,
    SodOutcome,
    SodPolicyBlocked,
)
from app.identity.service.institution_profile import get_full_profile, orass_institution_code
from app.identity.service.integration_keys import lock_authenticated_key
from app.identity.service.membership import ensure_baseline_membership
from app.identity.service.organization_ownership import assign_initial_owner
from app.identity.service.scoped_authorization import (
    DEFAULT_DENIAL_DETAIL,
    evaluate_bank_permission,
    require_bank_permission,
    require_bank_permission_prefetched,
    require_resolved_bank_permission,
    resolve_bank,
)
from app.identity.service.sso_config import find_enabled_by_issuer_audience

__all__ = [
    "AuthorizationBinding",
    "Bank",
    "BankLicense",
    "BankNameHistory",
    "BankProduct",
    "BankRead",
    "BankReference",
    "BankReportingPeriodRead",
    "DEFAULT_DENIAL_DETAIL",
    "EffectiveDataScope",
    "InstitutionProfile",
    "InstitutionProfileFullRead",
    "InstitutionProfileRead",
    "IntegrationKey",
    "Outlet",
    "RelatedParty",
    "RelatedPartyRead",
    "RelatedPartyRole",
    "Shareholding",
    "SodDecision",
    "SodDecisionRead",
    "SodFinding",
    "SodOutcome",
    "SodPolicyBlocked",
    "SodPolicyFindingRead",
    "SsoConnection",
    "User",
    "assign_initial_owner",
    "binding_is_effective",
    "burn_password_check",
    "clear_failures",
    "effective_data_scope",
    "ensure_baseline_membership",
    "evaluate_bank_permission",
    "evaluate_permission",
    "evaluate_prefetched_permission",
    "find_enabled_by_issuer_audience",
    "get_bank_or_404",
    "get_full_profile",
    "institution_grain_decision",
    "load_effective_grants",
    "lock_authenticated_key",
    "lock_expiry",
    "minutes_remaining",
    "orass_institution_code",
    "prefetch_principal_bindings",
    "principal_locator",
    "record_binding_decision",
    "record_binding_evaluation_failure",
    "record_failure",
    "record_failure_for",
    "record_success",
    "reduce_data_scope",
    "request_wide_condition_checks",
    "require_bank_permission",
    "require_bank_permission_prefetched",
    "require_resolved_bank_permission",
    "resolve_bank",
    "resolve_bank_reference",
]
