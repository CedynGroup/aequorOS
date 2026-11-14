"""The mutation boundary: P0-2 (read-guarded mutations) and P0-3 (document delete).

Two invariants, enforced in two different places, tested here together because
they share one failure mode — a route author picking ``ctx: Tenant`` for a
mutation:

1. **Absolute, structural.** An act-as-examiner impersonation session may NEVER
   answer an unsafe HTTP method. Enforced at the authentication boundary
   (``deps.refuse_impersonated_mutation``, called from ``get_current_principal``
   and again from ``get_tenant_db_session``), so it holds regardless of which
   ``ctx`` dependency a route declares. The one documented exemption is
   ``deps.IMPERSONATION_READ_ONLY_ROUTES``.
2. **Role ladder.** ``viewer`` and ``examiner`` are read-only. This one is NOT
   structural — it is carried by the route's own ``MutationTenant`` declaration —
   so the route-table sweep below is what keeps it honest: it is the test that
   would have caught P0-2.

The earlier suite (``test_impersonation_auth.py``) sampled five paths that all
already used ``MutationTenant``, so it could not see the defect. This one walks
the whole table.

This module's other-suite cases live in ``test_impersonation_boundary_integration.py``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import IMPERSONATION_READ_ONLY_ROUTES, MUTATION_ROLE_DEPENDENCY_NAMES
from tests.api.test_impersonation_boundary_integration import FIXED_MUTATION_ROUTES, _unsafe_routes

# Every unsafe-method route that still resolves only the read-only ``Tenant``
# context, with the reason each is tolerated. The sweep asserts the read-guarded
# set is a SUBSET of this list, so a concurrent owner tightening one of them
# shrinks the set and keeps the test green, while any NEW read-guarded mutation
# fails it.
#
# Note the audit's own count of 14 was taken WITHOUT crediting the explicit
# explicit admin dependencies on the attestation, integration-key and
# SSO-administration routes; those were never viewer-writable. With those gates
# credited (see ``_is_role_guarded``), these entries are the whole residue.
_UNFIXED_READ_GUARDED_UNSAFE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Legitimately viewer-writable: a user editing their OWN profile. Raising
        # it to the analyst floor would stop a viewer changing their own name.
        ("PATCH", "/api/v1/auth/me"),
        # Legitimately member-writable: this records a pending request for the
        # caller and grants no authority. The handler requires an active human
        # identity and derives the requested permission from a public route.
        ("POST", "/api/v1/authorization/access-requests"),
        # Genuinely read-only compute; see IMPERSONATION_READ_ONLY_ROUTES.
        ("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis"),
    }
)


def _is_role_guarded(names: set[str]) -> bool:
    """True if the route requires a write role — via ``MutationTenant``, or via
    an explicit ``require_role`` of analyst or above."""
    return bool(
        names & {"get_mutation_tenant_context"} or names & MUTATION_ROLE_DEPENDENCY_NAMES
    )


# --- 1. the structural sweep --------------------------------------------------
def test_no_new_mutation_hides_behind_the_read_guard(client: TestClient) -> None:
    """The test that would have caught P0-2: walk the whole route table.

    Every unsafe-method route must either carry a role gate, or be one of the
    known-unfixed routes owned by another stream, or have no tenant context at
    all (login/refresh/SSO, and the two no-op 404 rejectors that take no dependency).
    """
    offenders: list[tuple[str, str]] = []
    for method, path, names in _unsafe_routes(client):
        if _is_role_guarded(names):
            continue
        if "get_current_principal" not in names:
            continue  # unauthenticated by design — no tenant context to guard
        if (method, path) in _UNFIXED_READ_GUARDED_UNSAFE_ROUTES:
            continue
        offenders.append((method, path))
    assert offenders == [], (
        "these mutations are behind the read-only Tenant guard, so a viewer can "
        f"perform them: {offenders}"
    )


def test_the_fourteen_audited_routes_now_carry_a_role_gate(client: TestClient) -> None:
    guards = {
        (method, path): _is_role_guarded(names) for method, path, names in _unsafe_routes(client)
    }
    for entry in FIXED_MUTATION_ROUTES:
        assert entry in guards, f"{entry} is no longer in the route table"
        assert guards[entry], f"{entry} is still behind the read-only Tenant guard"


def test_impersonation_exemption_set_is_minimal_and_real(client: TestClient) -> None:
    """Every exemption must name a route that exists and is an unsafe method."""
    table = {(method, path) for method, path, _ in _unsafe_routes(client)}
    assert table >= IMPERSONATION_READ_ONLY_ROUTES, (
        "stale exemption entries: "
        f"{sorted(IMPERSONATION_READ_ONLY_ROUTES - table)}"  # pragma: no cover
    )
    # Deliberately pinned: widening this hole is a reviewable decision, not a
    # drive-by edit. ``analysis_workbench.run_analysis`` persists nothing.
    assert (
        frozenset({("POST", "/api/v1/banks/{bank_id}/scenario-workbench/{module}/analysis")})
        == IMPERSONATION_READ_ONLY_ROUTES
    )
