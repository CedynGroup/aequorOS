"""Platform IDs ARE the institution identity — on the ACTUAL primary.

Banks and organizations are keyed by short platform-generated identifiers
(BK-XXXXXXXX / OR-XXXXXXXX, Crockford base32) — the primary key, the API path
token, and the ID banks integrate with. No UUID aliases. Invariants: generator
format/charset/uniqueness (pure), the real tenant's ids obey the contract, the
bank's id IS its platform code (no alias field), lowercase input resolves, a
foreign tenant cannot resolve or probe it (404), unknown references 404 cleanly,
and the push surface opens on the platform id. DB-backed tests are opt-in via
REAL_DATA_DATABASE_URL and rolled back (tests/real_data.py).

This module's other-suite cases live in ``test_public_ids_integration.py``.
"""

from __future__ import annotations

from app.services.public_ids import (
    generate_public_id,
    is_public_id,
    new_bank_public_id,
    new_organization_public_id,
)
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_OTHER_BANK_ID,
    REAL_OTHER_ORG_ID,
)


def test_generator_format_and_charset() -> None:
    for _ in range(200):
        bank_code = new_bank_public_id()
        org_code = new_organization_public_id()
        assert is_public_id(bank_code)
        assert is_public_id(org_code)
        assert bank_code.startswith("BK-")
        assert org_code.startswith("OR-")
        # Ambiguous Crockford exclusions never appear.
        assert not set(bank_code[3:]) & set("ILOU")


def test_real_tenant_ids_are_valid_platform_ids() -> None:
    """The real primary's tenants (both sides of the isolation pair) obey the
    same format contract the generator promises."""
    for code in (REAL_BANK_ID, REAL_OTHER_BANK_ID):
        assert is_public_id(code)
        assert code.startswith("BK-")
    for code in (REAL_ORG_ID, REAL_OTHER_ORG_ID):
        assert is_public_id(code)
        assert code.startswith("OR-")


def test_is_public_id_rejects_non_platform_forms() -> None:
    assert not is_public_id("77000000-0000-4000-8000-000000000001")
    assert not is_public_id("seed-demo")
    assert not is_public_id("BK-SHORT")
    assert not is_public_id("XX-ABCDEFGH")


def test_generate_public_id_uniqueness_sample() -> None:
    codes = {generate_public_id("BK") for _ in range(5000)}
    assert len(codes) == 5000
