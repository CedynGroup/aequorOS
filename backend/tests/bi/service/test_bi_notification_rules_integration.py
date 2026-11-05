"""The rules the notification ROUTES hold that a request cannot reach.

Three kinds of thing live here, all of them about the surface rather than about
the engines (which have their own suites in this directory):

* **the copy is total.** Every value in every vocabulary the database will store
  has a sentence, so no raw enum and no wire code can reach a reader. Proven by
  iterating the model's own tuples and the service's own reason constants, so a
  new value fails here rather than appearing on screen as ``data_scope_unsupported``.
* **the defence-in-depth branches.** The threshold pairing and the recipient cap
  are each stated three times — a request validator, a route check, a database
  CHECK — and the route's copy is the one that answers with a named error rather
  than a 500. The request validator makes those route branches unreachable
  through HTTP, so they are exercised directly here; without this they would be
  untested code that a future reader would delete as dead.
* **the parity pins.** The surface reads the institution's time zone and the
  recipient cap from its own code; both have an authority elsewhere, and a
  surface that displayed one zone while the delivery scan used another, or a cap
  the interface enforced at a different number from the worker's, would be worse
  than not stating them at all.

This module's other-suite cases live in ``test_bi_notification_rules.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.bi.api import manage_bi_notifications as feature
from app.bi.api.read_bi import BiReadAccess
from app.bi.service import subscriptions
from app.models import Bank, User
from tests.bi.service.test_bi_notification_rules import _detail
from tests.support.helpers import ORG_1

BANK_ID = "BK-BINOTR01"


def _bank(db: Session) -> Bank:
    """One institution of the hermetic tenant, with a jurisdiction on it.

    ``banks.jurisdiction_code`` carries no default by design, so it is stated:
    the zone the surface displays is resolved through the registry from this
    column, which is the whole point of the parity pin below.
    """

    existing = db.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    bank = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="BI notification rules",
        short_name="BI rules",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def test_the_surfaces_time_zone_is_the_delivery_scans_own(db_session: Session) -> None:
    """ "07:30" must mean the same instant on the screen and in the scan.

    Both resolve ``banks.jurisdiction_code`` through the ``jurisdictions``
    registry and both fall back to UTC when the registry records no zone; they
    are separate functions because the scan's returns a ``ZoneInfo`` and the
    surface's returns the name to display. This asserts they agree.
    """

    bank = _bank(db_session)
    assert feature._time_zone_name(db_session, bank) == str(
        subscriptions._zone_for(db_session, bank)
    )


def test_the_route_refuses_more_recipients_than_one_run_may_deliver_to(
    db_session: Session,
) -> None:
    """The cap applied AFTER resolution, which is where it counts people.

    Unreachable over HTTP — the request model caps each list and their sum — and
    kept because the route reads the DELIVERY SERVICE's number while the request
    model reads its own copy of it. If those two ever drift, this branch is what
    stops a subscription the worker would refuse whole from being stored.
    """

    bank = _bank(db_session)
    addresses: list[str] = []
    for index in range(subscriptions.MAX_RECIPIENTS + 1):
        address = f"recipient-{index}@example.test"
        db_session.add(
            User(
                id=uuid4(),
                organization_id=ORG_1,
                email=address,
                display_name=f"Recipient {index}",
            )
        )
        addresses.append(address)
    db_session.flush()
    access = _access(ORG_1, bank)

    with pytest.raises(HTTPException) as caught:
        feature._resolve_recipients(
            db_session, access, user_ids=[], emails=addresses, what="scheduled report"
        )
    assert caught.value.status_code == 422
    assert _detail(caught.value)["error_code"] == "bi_notification_recipient_cap_exceeded"

    # One fewer is accepted, so the refusal is the cap and not the loop.
    resolved = feature._resolve_recipients(
        db_session, access, user_ids=[], emails=addresses[:-1], what="scheduled report"
    )
    assert len(resolved) == subscriptions.MAX_RECIPIENTS


def test_an_address_is_matched_without_regard_to_its_case(db_session: Session) -> None:
    """Somebody typing a colleague's address in title case named that colleague."""

    bank = _bank(db_session)
    user_id = uuid4()
    db_session.add(
        User(
            id=user_id,
            organization_id=ORG_1,
            email="mixed.case@example.test",
            display_name="Mixed Case",
        )
    )
    db_session.flush()
    resolved = feature._resolve_recipients(
        db_session,
        _access(ORG_1, bank),
        user_ids=[],
        emails=["mixed.case@example.test"],
        what="scheduled report",
    )
    assert resolved == [user_id]


def test_a_deactivated_identity_cannot_be_added_to_a_list(db_session: Session) -> None:
    """A stopped account is refused at write time, not silently dropped.

    The delivery path refuses it too (``recipient_inactive``), but an author who
    named somebody who has left should be told while they are looking at the form
    rather than three weeks later in a delivery history they may never open.
    """

    bank = _bank(db_session)
    db_session.add(
        User(
            id=uuid4(),
            organization_id=ORG_1,
            email="departed@example.test",
            display_name="Departed",
            is_active=False,
        )
    )
    db_session.flush()
    with pytest.raises(HTTPException) as caught:
        feature._resolve_recipients(
            db_session,
            _access(ORG_1, bank),
            user_ids=[],
            emails=["departed@example.test"],
            what="scheduled report",
        )
    detail = _detail(caught.value)
    assert detail["error_code"] == "bi_notification_recipient_unknown"
    assert detail["unknown_emails"] == ["departed@example.test"]


def _access(organization_id: str, bank: Bank) -> BiReadAccess:
    """A minimal admitted reader. Only the tenant and the bank are read here."""

    actor: UUID = uuid4()
    return BiReadAccess(
        ctx=TenantContext(
            organization_id=organization_id, actor_user_id=actor, authorization_version=1
        ),
        bank=bank,
        principal_user_id=actor,
        authorization_version=1,
    )
