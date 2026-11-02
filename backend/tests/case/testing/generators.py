"""Scoped binding sets from case_enforcement_rollout.md §Authority contract.

Human bundles follow authorization_foundation.md §Fixed role bundles. Machine
bundles are exercised separately with machine credentials; they are invalid on a
human identity. Membership and audit-officer scopes retain their grant invariants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hypothesis import strategies as st

from app.core.authorization import InstitutionScope, ModuleScope, RoleBundle, SensitivityScope

type Lifecycle = Literal["active", "future", "expired", "suspended", "revoked"]


@dataclass(frozen=True)
class CaseBinding:
    bundle: RoleBundle
    module: ModuleScope = ModuleScope.RISK
    sensitivity: SensitivityScope = SensitivityScope.ALL
    institution: InstitutionScope = InstitutionScope.ORGANIZATION
    lifecycle: Lifecycle = "active"


@st.composite
def case_bindings(draw: st.DrawFn) -> CaseBinding:
    """Construct legal human grant shapes, including inactive/irrelevant rows."""
    bundle = draw(
        st.sampled_from(
            tuple(
                bundle
                for bundle in RoleBundle
                if bundle not in (RoleBundle.INTEGRATION_WRITER, RoleBundle.BI_READER)
            )
        )
    )
    module = draw(st.one_of(st.just(ModuleScope.RISK), st.sampled_from(tuple(ModuleScope))))
    sensitivity = draw(st.sampled_from(tuple(SensitivityScope)))
    institution = draw(st.sampled_from(tuple(InstitutionScope)))
    lifecycle = draw(st.sampled_from(("active", "future", "expired", "suspended", "revoked")))
    if bundle is RoleBundle.MEMBER:
        module, sensitivity = ModuleScope.ACCOUNT, SensitivityScope.RESTRICTED
        institution, lifecycle = InstitutionScope.ORGANIZATION, "active"
    elif bundle in (RoleBundle.SECURITY_OFFICER, RoleBundle.COMPLIANCE_OFFICER):
        module = ModuleScope.AUDIT
    return CaseBinding(bundle, module, sensitivity, institution, lifecycle)


def case_binding_sets() -> st.SearchStrategy[list[CaseBinding]]:
    """Empty, unrelated, complete and split grants; rows never compose authority."""
    return st.one_of(
        st.lists(case_bindings(), max_size=4),
        st.lists(
            st.builds(
                CaseBinding,
                bundle=st.sampled_from(
                    (RoleBundle.VIEWER, RoleBundle.ANALYST, RoleBundle.APPROVER)
                ),
                sensitivity=st.sampled_from(tuple(SensitivityScope)),
            ),
            min_size=1,
            max_size=3,
        ),
    )
