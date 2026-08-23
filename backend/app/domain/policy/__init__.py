"""Policy & parameter governance (shared primitive P2).

The single resolution chain every governed regulatory number goes through::

    Jurisdiction -> Regulator -> Institution Type -> Regime -> Return Family
                 -> Parameter Set -> Effective Date

Pure by construction (no SQLAlchemy / FastAPI / ``app.services``). The database
adapter is ``app/services/regulatory_parameters.py``; import THAT when you have a
``Session`` and a ``Bank``, and import from here when you have plain values.
"""

from app.domain.policy.resolver import (
    PARAMETER_DIRECTION,
    ClampRecord,
    ClampReport,
    Direction,
    ParameterCandidate,
    PolicyLayer,
    PolicyResolution,
    PolicyScope,
    PolicyUnresolvedError,
    clamp_overrides,
    direction_for,
    from_candidate,
    governed_codes,
    is_active_on,
    policy_unresolved,
    resolution_order,
    select_active,
    tighten,
)

__all__ = [
    "PARAMETER_DIRECTION",
    "ClampRecord",
    "ClampReport",
    "Direction",
    "ParameterCandidate",
    "PolicyLayer",
    "PolicyResolution",
    "PolicyScope",
    "PolicyUnresolvedError",
    "clamp_overrides",
    "direction_for",
    "from_candidate",
    "governed_codes",
    "is_active_on",
    "policy_unresolved",
    "resolution_order",
    "select_active",
    "tighten",
]
