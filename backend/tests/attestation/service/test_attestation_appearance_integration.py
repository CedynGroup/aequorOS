"""Adopted signature marks — normalisation and the guarantee it replaces.

``pdf_signing`` used to promise "nothing user-supplied reaches the page". A drawn
signature is user-supplied by definition, so the promise now reads "normalised
raster only". These tests are the executable form of that narrower claim: each one
would be a DEFECT if it changed, because each is something the old guarantee used
to cover for free.

This module's other-suite cases live in ``test_attestation_appearance.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.service import appearance
from app.attestation.service.appearance import (
    NORMALISED_HEIGHT,
    NORMALISED_WIDTH,
    SignatureMarkRejected,
)
from app.models import AdoptedSignatureAppearance, AuditEvent
from tests.attestation.service.test_attestation_appearance import _png, _strokes
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    materialize_canonical_test_book,
)

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
SIGNER_ID = "SGN-7K4M9PQR2VWX3YZ8"


def test_adoption_stores_only_normalised_bytes_and_audits_the_act(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    raw = _png(_strokes((640, 240)))
    row = appearance.adopt(db_session, CTX, signer_id=SIGNER_ID, kind="drawn", drawn=raw)
    db_session.commit()

    assert row.image_png is not None
    assert row.image_png != raw
    assert (row.image_width, row.image_height) == (NORMALISED_WIDTH, NORMALISED_HEIGHT)
    assert row.typed_name is None
    events = [
        event
        for event in db_session.query(AuditEvent).all()
        if event.event_type == "attestation.signature_appearance_adopted"
    ]
    assert len(events) == 1
    # The trail records that a mark was adopted, never the mark itself.
    assert "image_png" not in events[0].details
    assert events[0].details["kind"] == "drawn"


def test_re_adoption_replaces_the_mark_and_leaves_a_second_audit_event(
    db_session: Session,
) -> None:
    """Presentation, not evidence: an officer may change their own mark.

    Forcing a void to fix a badly drawn signature would make the mark behave like
    evidence, which it is not — the signature commits to the digest and the signed
    bytes. The audit trail is what preserves the history instead.
    """
    materialize_canonical_test_book(db_session)
    appearance.adopt(db_session, CTX, signer_id=SIGNER_ID, kind="drawn", drawn=_png(_strokes()))
    db_session.commit()
    appearance.adopt(
        db_session,
        CTX,
        signer_id=SIGNER_ID,
        kind="typed",
        typed_name="Ama Mensah",
        typed_font="times_italic",
    )
    db_session.commit()

    rows = db_session.query(AdoptedSignatureAppearance).all()
    assert len(rows) == 1
    assert rows[0].kind == "typed"
    # Switching kinds must clear the other kind's payload, or the renderer would
    # have to guess which mark the officer actually adopted.
    assert rows[0].image_png is None
    assert rows[0].typed_name == "Ama Mensah"
    assert (
        len(
            [
                event
                for event in db_session.query(AuditEvent).all()
                if event.event_type == "attestation.signature_appearance_adopted"
            ]
        )
        == 2
    )


def test_a_rejected_mark_never_replaces_a_good_one(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    appearance.adopt(
        db_session,
        CTX,
        signer_id=SIGNER_ID,
        kind="typed",
        typed_name="Ama Mensah",
        typed_font="times_italic",
    )
    db_session.commit()
    with pytest.raises(SignatureMarkRejected):
        appearance.adopt(db_session, CTX, signer_id=SIGNER_ID, kind="drawn", drawn=b"not an image")
    db_session.rollback()

    current = appearance.get_adopted(db_session, CTX, SIGNER_ID)
    assert current is not None
    assert current.kind == "typed"
    assert current.typed_name == "Ama Mensah"
