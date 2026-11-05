"""An examination reads the filed registers it is bound to — and nothing else.

Issue 182, captain decision P1. The six registers below are the ones the examiner
documentation package CITES instead of copying their rows
(``app.reporting.service.examiner_access.SOURCE_REGISTERS``). A staff inspection
session holds no tenant binding at all, so before this contract every one of them
answered 403: the bundle disclosed a catalogue its reader could not open.

The widening is narrow and it is a widening of REACH only:

* the session is bound to ONE bank, recorded when the session was opened and
  carried in the signed token, and that bank must be the bank in the path;
* the route must be one of the six (``app.core.examiner``), by method and
  templated path, so a gate shared with an uncited register admits only the
  cited one;
* the session must be a verified read-only inspection, not merely an
  impersonation claim;
* a tenant principal gains nothing: the binding sentence below is unchanged, and
  the examiner admission is not a fallback for a missing grant;
* every register PUT stays refused (an examination mutates nothing).
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.examiner import EXAMINER_REGISTER_ROUTES
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationBinding
from app.models import Bank
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.helpers import ORG_1, headers

#: A second institution of the SAME organization: an examination of the sibling
#: must not read this bank's registers (two banks of one org share an RLS tenant).
SIBLING_BANK_ID = "BK-EXAM0002"
BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING_BASE = f"/api/v1/banks/{SIBLING_BANK_ID}"

#: The cited registers, as ``(method, templated path)`` — the same routes the
#: authority table names in
#: ``tests/architecture/test_credit_route_authorization.py``.
CITED = sorted(EXAMINER_REGISTER_ROUTES)
CITED_PATHS = sorted({path.format(bank_id=SAMPLE_BANK_ID) for _, path in CITED})


@pytest.fixture(autouse=True)
def canonical_examination_book(db_client: TestClient, db_session: Session) -> None:
    """The canonical book, one sibling institution, and no ambient authority.

    The shared hermetic fixture grants ``USER_1`` a viewer/all/all row; the
    tenant-principal half of this contract starts from zero authority so a 403 is
    the sentence being tested rather than a leftover grant."""
    _ = db_client
    materialize_canonical_test_book(db_session)
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    if db_session.get(Bank, SIBLING_BANK_ID) is None:
        db_session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="Sibling Bank Ltd",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type="universal_bank",
            )
        )
    db_session.commit()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _examiner(bank_id: str | None = SAMPLE_BANK_ID) -> dict[str, str]:
    """An act-as-examiner session bound to ``bank_id`` (None = a legacy session).

    Minted directly with the dedicated impersonation secret the tenant API
    verifies (pinned by the root conftest), exactly as the operator service mints
    it — a faithful stand-in that keeps the accept path under test."""
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret, "the impersonation secret must be configured for this helper"
    now = utc_now()
    return _bearer(
        security.mint_impersonation_token(
            organization_id=ORG_1,
            act_operator="examiner@regulator.test",
            session_id="11111111-2222-4333-8444-555555555555",
            secret=secret,
            issued_at=now,
            expires_at=now + dt.timedelta(minutes=15),
            bank_id=bank_id,
        )
    )


def _path(templated: str, bank_id: str = SAMPLE_BANK_ID) -> str:
    return templated.format(bank_id=bank_id)


def _register_id(register: tuple[str, str]) -> str:
    """Readable test ids: ``GET /api/v1/banks/{bank_id}/…``."""
    return f"{register[0]} {register[1]}"


@pytest.mark.parametrize("register", CITED, ids=_register_id)
def test_a_bound_examination_reads_every_cited_register(
    db_client: TestClient, register: tuple[str, str]
) -> None:
    """The bundle's disclosures are readable by the session it is served to."""
    method, templated = register
    path = _path(templated)
    response = db_client.request(method, path, headers=_examiner())
    assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"


@pytest.mark.parametrize("register", CITED, ids=_register_id)
def test_an_examination_of_another_bank_reads_nothing(
    db_client: TestClient, register: tuple[str, str]
) -> None:
    """The bank in the path must be the bank the session examined.

    Two banks of one organization share an RLS tenant, so organization scoping
    alone cannot isolate them: the signed bank claim is the boundary."""
    method, templated = register
    path = _path(templated)
    response = db_client.request(method, path, headers=_examiner(SIBLING_BANK_ID))
    assert response.status_code == 403, f"{method} {path}: {response.text}"


@pytest.mark.parametrize("register", CITED, ids=_register_id)
def test_a_session_without_a_bank_claim_reads_nothing(
    db_client: TestClient, register: tuple[str, str]
) -> None:
    """A legacy session (opened before the bank was recorded) binds no bank.

    There is deliberately no organization-wide fallback: a session that names no
    institution examines no institution."""
    method, templated = register
    path = _path(templated)
    response = db_client.request(method, path, headers=_examiner(None))
    assert response.status_code == 403, f"{method} {path}: {response.text}"


def test_the_binding_is_exact_in_both_directions(db_client: TestClient) -> None:
    """An examination of THIS bank cannot read the sibling's register either."""
    response = db_client.get(
        f"{SIBLING_BASE}/ecl-assumptions",
        headers=_examiner(SAMPLE_BANK_ID),
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    "path",
    [
        # The two credit registers the bundle does NOT cite.
        f"{BASE}/concentration-limits",
        f"{BASE}/credit-thresholds",
        # Diagnostics that disclose the book, not registers.
        f"{BASE}/sdi/large-exposures",
        f"{BASE}/sdi/loan-classification",
        f"{BASE}/loan-classification",
        # A capital figure that is not a cited register.
        f"{BASE}/capital-plan/ilaap",
        # A liquidity read on the same dependency as two cited registers.
        f"{BASE}/liquidity/cfp",
    ],
)
def test_uncited_surfaces_stay_refused_for_an_examination(
    db_client: TestClient, path: str
) -> None:
    """Only the six cited registers are admitted — never the neighbouring reads.

    These matter because they share a dependency with a cited register
    (``/concentration-limits`` and ``/sdi/large-exposures`` take
    ``require_credit_restricted_view``; ``/liquidity/cfp`` takes the liquidity
    confidential view), so an admission keyed on the DEPENDENCY would have
    opened them by accident."""
    response = db_client.get(path, headers=_examiner())
    assert response.status_code == 403, f"{path}: {response.status_code} {response.text}"


def test_a_tenant_principal_still_needs_the_exact_binding(db_client: TestClient) -> None:
    """The examination admission is not a fallback for a missing tenant grant."""
    for path in CITED_PATHS:
        tenant = db_client.get(path, headers=headers())
        assert tenant.status_code == 403, f"tenant {path}: {tenant.text}"
        examiner = db_client.get(path, headers=_examiner())
        assert examiner.status_code == 200, f"examiner {path}: {examiner.text}"


@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/liquidity-thresholds",
        f"{BASE}/liquidity-haircuts",
        f"{BASE}/liquidity/ewis",
        f"{BASE}/ecl-assumptions",
        f"{BASE}/crm-haircuts",
        f"{BASE}/capital-plan",
    ],
)
def test_an_examination_mutates_nothing(db_client: TestClient, path: str) -> None:
    """The register writes are #184's Configurator work and stay refused here."""
    response = db_client.put(path, json={}, headers=_examiner())
    assert response.status_code == 403, f"PUT {path}: {response.status_code} {response.text}"
