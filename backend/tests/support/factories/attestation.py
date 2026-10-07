"""Test helpers for the signing gate.

Signing is REQUIRED for every return by default, so any suite that drives a
package all the way to a channel now meets the attestation gate on the way. Two
honest ways past it exist, and they are not interchangeable:

- :func:`relax_signing` writes the same signature-optional policy row an
  administrator would write in Settings. Suites that are about *something else*
  (channel behaviour, regulator decisions, export fidelity) use this, so they
  keep testing their own subject instead of silently becoming attestation tests.
- Completing the ceremony for real. That is what the attestation suites and the
  Playwright lifecycle journey do, and it is where "a signed return can be
  filed, an unsigned one cannot" is actually proved.

Reaching for :func:`relax_signing` in a suite that is *about* the filing gate
would be a way of deleting the gate while keeping the tests green — so its use
is deliberately explicit at every call site rather than a global fixture.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import ReturnSigningPolicy

#: Far enough back that any reporting date in a fixture resolves to it.
_EFFECTIVE_FROM = date(2000, 1, 1)


def relax_signing(
    db: Session,
    *,
    organization_id: str,
    return_code: str | None = None,
    return_family: str | None = None,
    reason: str = "test fixture: this suite is not about the signing gate",
) -> ReturnSigningPolicy:
    """Configure a signature-OPTIONAL policy, the way an administrator would.

    Scope it as narrowly as the suite needs: ``return_code`` beats
    ``return_family``, matching production resolution order.
    """
    if return_code is None and return_family is None:
        msg = "a relaxed policy must name a return_code or a return_family"
        raise ValueError(msg)
    row = ReturnSigningPolicy(
        organization_id=organization_id,
        return_code=return_code,
        return_family=return_family,
        required_signatures=[],
        require_signature=False,
        effective_from=_EFFECTIVE_FROM,
        reason=reason,
    )
    db.add(row)
    db.flush()
    return row
