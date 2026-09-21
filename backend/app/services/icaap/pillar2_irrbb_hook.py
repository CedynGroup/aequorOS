"""Does a superseded IRRBB method still block this freeze?

The IRRBB standardised framework becomes mandatory from a governed as-of date.
Once it is live, an ICAAP whose IRRBB Pillar 2 figure came from the interim
method is filing a number the supervisor no longer accepts, and the freeze says
so rather than sealing it.

It stays a named module rather than an inline check for the reason it was
written: the freeze reads the same before and after the mandate, and there is
exactly one place the rule lives.

**Which method the mandate supersedes is the FRAMEWORK's answer, not this
module's.** A Pillar 2 component declares its own ``method_mandates`` (A6):
the method that becomes mandatory, the governed code carrying the date, and
the method it replaces. Ghana's ICAAP framework carries one; Nigeria's and
Kenya's carry none, so a missing Ghanaian row can never block a Nigerian
filing. Nothing here names a method, a date or a country.

**The read records no provenance.** A freeze preflight is a DISPATCH read that
seals no run; the commencement row it resolves must not enter the session's
consumption ledger, or the package minted moments later would carry a governed
row it never consumed inside the ``content_digest`` an officer signs (D-078).
:mod:`app.services.icaap.sf_state` resolves it with ``record=False``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models.icaap import IcaapCycle
from app.services.icaap import guards, sf_state

#: The refusal the freeze reports. It is the readiness code, deliberately: the
#: preparer has been looking at this finding on the checklist, and a freeze
#: that refused under a different name would read as a second, unrelated
#: problem.
INTERIM_NOT_PERMITTED = "irrbb_interim_not_permitted"


def interim_blocking(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, *, today: date | None = None
) -> str | None:
    """A blocking reason code, or ``None`` when the interim method is still fine."""
    if cycle.cycle_kind == "rehearsal":
        # A rehearsal is never filed, so it may be run against the old method
        # deliberately. Readiness still warns.
        return None
    framework, _digest_matches = guards.framework_for(cycle)
    decisions = sf_state.method_mandates(
        db,
        access.bank,
        framework,
        as_of=cycle.as_of_date,
        today=today or date.today(),
    )
    if not decisions:
        return None
    methods = sf_state.item_methods(db, access, cycle)
    for decision in decisions:
        if not decision.mandatory or decision.replaces is None:
            continue
        if any(
            methods.get(component) == decision.replaces
            for component in decision.component_keys
        ):
            return INTERIM_NOT_PERMITTED
    return None


__all__ = ["INTERIM_NOT_PERMITTED", "interim_blocking"]
