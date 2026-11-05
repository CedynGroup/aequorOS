"""The examiner register contract: which filed registers an examiner may read.

The examiner evidence bundle (``app/reporting``) cites six register endpoints
rather than duplicating their rows (``SOURCE_REGISTERS``). An admitted
act-as-examiner session must therefore be able to READ exactly those six, and
nothing else, in the ONE bank its inspection session is bound to.

The list lives here, in the kernel, for two reasons:

* it is the same data two layers need — ``app/api/deps.py``, the kernel gate
  that admits the session, and ``app/reporting/service/examiner_access.py``,
  which discloses the register catalogue — and neither may import the other;
* it is the allow-list itself, so a route that is not named here is not an
  examiner read however the route's own dependency is spelled.

An entry is ``(method, templated route path) -> the register it serves``, the
same shape ``IMPERSONATION_READ_ONLY_ROUTES`` uses. Membership is REACHABILITY,
never authority: an examiner session still has to be bank-bound, still has to be
a verified read-only inspection (``request_wide_condition_checks``), and still
has to name the bank it is examining. Tenant principals are unaffected — their
route keeps its exact CREDIT / LIQUIDITY / CAPITAL binding sentence.

Deliberately absent: ``/concentration-limits`` and ``/credit-thresholds`` (the
two credit registers the bundle does not cite), ``/sdi/large-exposures`` and
``/sdi/loan-classification`` (diagnostics, not registers), and every register
PUT. Adding one is a reviewed decision, and
``tests/architecture/test_examiner_register_routes.py`` pins this set.

Data only: no FastAPI, no SQLAlchemy, no feature imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.core.authorization import Module, Sensitivity


@dataclass(frozen=True)
class ExaminerRegister:
    """One cited register: its name, and the sentence a tenant reader needs."""

    name: str
    module: Module
    sensitivity: Sensitivity


#: The six cited registers, in the order the evidence bundle lists them, keyed by
#: ``(HTTP method, templated route path)``.
EXAMINER_REGISTER_ROUTES: Final[Mapping[tuple[str, str], ExaminerRegister]] = MappingProxyType(
    {
        ("GET", "/api/v1/banks/{bank_id}/liquidity-thresholds"): ExaminerRegister(
            "liquidity-thresholds", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL
        ),
        ("GET", "/api/v1/banks/{bank_id}/liquidity-haircuts"): ExaminerRegister(
            "liquidity-haircuts", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL
        ),
        ("GET", "/api/v1/banks/{bank_id}/liquidity/ewis"): ExaminerRegister(
            "liquidity/ewis", Module.LIQUIDITY, Sensitivity.CONFIDENTIAL
        ),
        ("GET", "/api/v1/banks/{bank_id}/ecl-assumptions"): ExaminerRegister(
            "ecl-assumptions", Module.CREDIT, Sensitivity.RESTRICTED
        ),
        ("GET", "/api/v1/banks/{bank_id}/crm-haircuts"): ExaminerRegister(
            "crm-haircuts", Module.CREDIT, Sensitivity.RESTRICTED
        ),
        ("GET", "/api/v1/banks/{bank_id}/capital-plan"): ExaminerRegister(
            "capital-plan", Module.CAPITAL, Sensitivity.CONFIDENTIAL
        ),
    }
)


def register_for(method: str, route_path: str | None) -> ExaminerRegister | None:
    """The cited register a request serves, or ``None`` when it serves none."""

    if route_path is None:
        return None
    return EXAMINER_REGISTER_ROUTES.get((method.upper(), route_path))
