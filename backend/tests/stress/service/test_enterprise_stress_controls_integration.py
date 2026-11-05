"""Service-layer controls for the enterprise stress run (P0-8, P0-9, P0-13).

Three defects from the 2026-08-20 enterprise audit, at the seam where the pure
engines meet the database:

* **P0-8** — the Basel HQLA haircuts and Level-2 caps must reach ``compute_lcr``
  from the regulatory-parameter control plane, never from a literal.
* **P0-9** — a scenario that does not carry every macro variable the run reads is
  refused BEFORE any engine runs, with the missing variables named.
* **P0-13** — a run whose base-case plan falls back to platform constants records
  that fact field by field, so a Board-attested ICAAP cannot silently rest on
  assumptions the institution never made.

This module's other-suite cases live in ``test_enterprise_stress_controls.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Bank
from app.policy.service import regulatory_parameters
from tests.support.helpers import ORG_1

AS_OF = date(2026, 6, 30)


def _bank(db: Session, *, institution_type: str = "universal_bank") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Control Test Bank",
        short_name="CTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_the_basel_hqla_haircuts_and_caps_resolve_from_the_control_plane(
    db_session: Session,
) -> None:
    """The engine names no rate; these are the seeded, cited, governed values."""
    hqla = regulatory_parameters.resolve_hqla_parameters(db_session, _bank(db_session), as_of=AS_OF)

    assert hqla.haircut_pct["L1"] == Decimal("0")
    assert hqla.haircut_pct["L2A"] == Decimal("15")
    assert hqla.haircut_pct["L2B"] == Decimal("50")
    assert hqla.level2_cap_pct == Decimal("40")
    assert hqla.level2b_cap_pct == Decimal("15")
    assert hqla.unresolved_codes == ()


def test_an_sdi_resolves_no_hqla_parameters(db_session: Session) -> None:
    """An SDI never runs the Basel LCR, so no HQLA rate is seeded for it."""
    sdi = _bank(db_session, institution_type="savings_and_loans")
    hqla = regulatory_parameters.resolve_hqla_parameters(db_session, sdi, as_of=AS_OF)
    assert hqla.haircut_pct == {}
    assert hqla.level2_cap_pct is None
    assert set(hqla.unresolved_codes) == {
        *regulatory_parameters.HQLA_HAIRCUT_CODES.values(),
        regulatory_parameters.HQLA_LEVEL2_CAP_CODE,
        regulatory_parameters.HQLA_LEVEL2B_CAP_CODE,
    }
