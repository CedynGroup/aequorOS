"""Server-side module scoping (docs/sdi.md §14; QA audit 2026-08-20 P2).

The frontend ``ModuleGuard`` hides bank-only modules for an SDI, but hiding is
not security. ``require_module_access`` enforces the institution-type module set
on the tenant API, so a savings-&-loans tenant cannot reach a bank-only module
(IRRBB / FX / FTP / forecasting / behavioral) by calling the endpoint directly.

The universal bank keeps full access — the guard denies on the scoped-out class,
never on the module existing.
"""

from __future__ import annotations

from app.db.session import get_sessionmaker
from app.models import Bank
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def _seed_universal_bank() -> str:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    return SAMPLE_BANK_ID


def _create_sdi_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="Module Scoping S&L",
            short_name="MSSL",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


# The bank-only module endpoints an SDI must be blocked from (one GET per gated
# router, all keyed on the same ``require_module_access`` dependency). FX, FTP and
# trading Positions are the treasury/trading-book modules an SDI does not get;
# IRRBB / behavioural / forecasting / market-data ARE in the SDI set (founder call
# 2026-08-21, migration 202608210026) and are covered by the positive test below.
_BANK_ONLY_ENDPOINTS = (
    "/api/v1/banks/{bank_id}/fx/dashboard",
    "/api/v1/banks/{bank_id}/ftp/dashboard",
)


def test_sdi_is_denied_bank_only_module_endpoints(db_client) -> None:  # noqa: ANN001
    _seed_universal_bank()  # initialize the app engine + reference rows
    sdi_bank_id = _create_sdi_bank()
    for template in _BANK_ONLY_ENDPOINTS:
        response = db_client.get(template.format(bank_id=sdi_bank_id), headers=headers())
        assert response.status_code == 403, f"{template} -> {response.status_code}: {response.text}"
        assert "not available for this institution" in response.text


def test_sdi_is_allowed_the_in_scope_alm_modules(db_client) -> None:  # noqa: ANN001
    """IRRBB is in the SDI module set, so the guard must NOT 403 an SDI on it
    (any other status — 200, 404 for absent data — is fine; only a scoping 403 is a
    bug). Proves the expanded SDI set (migration 202608210026) is honoured."""
    _seed_universal_bank()
    sdi_bank_id = _create_sdi_bank()
    response = db_client.get(
        f"/api/v1/banks/{sdi_bank_id}/irr/dashboard", headers=headers()
    )
    assert response.status_code != 403, f"IRRBB wrongly scoped out for an SDI: {response.text}"


def test_universal_bank_is_not_module_scoped_out(db_client) -> None:  # noqa: ANN001
    bank_id = _seed_universal_bank()
    for template in _BANK_ONLY_ENDPOINTS:
        response = db_client.get(template.format(bank_id=bank_id), headers=headers())
        # The module guard must NOT 403 a universal bank — any other status (200,
        # 404 for absent data, …) is acceptable; only a module-scoping 403 is a bug.
        assert response.status_code != 403, f"{template} wrongly scoped out for a bank"
