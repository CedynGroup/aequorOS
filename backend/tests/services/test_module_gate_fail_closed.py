"""P0-12: the API module gate must fail CLOSED on an unresolved licence class.

``require_module_access`` (``app/api/deps.py``) reads the entitled module set from
``institution_types.get_type(db, bank).default_modules``. Until 2026-08-21 that
resolver fell back to the ``universal_bank`` row on an unknown or blank
``institution_type`` — and ``universal_bank.default_modules`` is the FULL module
set, so a typo in the discriminator did not deny access, it granted every
bank-only module. That is a security control failing open.

These tests pin the inverted behaviour at both levels: the resolver refuses, and
a request for a gated module on such a tenant is rejected with an actionable
message rather than served or 500-ed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import Bank
from app.services import institution_types
from tests.api.helpers import ORG_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

# A gated bank-only module endpoint (same dependency as every other gated router).
GATED_ENDPOINT = "/api/v1/banks/{bank_id}/fx/dashboard"


def _create_bank_with_unresolvable_type() -> str:
    """A tenant whose discriminator does not resolve.

    Written with the FK check relaxed because the point of the test is the
    application-level control: the resolver must not substitute a regime even
    when a bad value reaches the column (an unseeded registry, a pre-FK row, or
    any deployment where the constraint is not enforced).
    """
    session = get_sessionmaker()()
    try:
        session.connection().exec_driver_sql("PRAGMA foreign_keys=OFF")
        bank = Bank(
            organization_id=ORG_1,
            name="Unresolvable Type Bank",
            short_name="UTB",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="not_a_real_licence_class",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def test_default_modules_refuses_rather_than_returning_the_bank_superset(
    db_session: Session,
) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Unresolvable Type Bank",
        short_name="UTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="not_a_real_licence_class",
    )
    with pytest.raises(institution_types.InstitutionTypeUnresolved):
        institution_types.default_modules(db_session, bank)


def test_module_gate_denies_a_tenant_whose_licence_class_does_not_resolve(
    db_client,  # noqa: ANN001
) -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()
    assert SAMPLE_BANK_ID  # the app engine + reference rows are initialised

    bank_id = _create_bank_with_unresolvable_type()
    response = db_client.get(GATED_ENDPOINT.format(bank_id=bank_id), headers=headers())

    # Denied, and denied with a precise reason — not granted, and not a bare 500.
    assert response.status_code == 409, f"{response.status_code}: {response.text}"
    assert "not in the institution_types registry" in response.text
    assert "is NOT treated as a universal bank" in response.text
