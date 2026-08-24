"""Capture content decoding and value snippets."""

from __future__ import annotations

import base64
import hashlib
from datetime import date

from sqlalchemy.orm import Session

from app.core.ids import new_uuid7
from app.models import DeskSourceCapture
from app.services.market_desk import snippets


def test_html_snippet_around_value(db_session: Session) -> None:
    html = b"<html><body><table><tr><td>MPR</td><td>15.00</td></tr></table></body></html>"
    capture = DeskSourceCapture(
        source_key="bog_mpr",
        as_of_date=date(2026, 8, 7),
        source_url="https://example.test/mpr",
        content_sha256="a" * 64,
        payload={
            "content_base64": base64.b64encode(html).decode("ascii"),
            "meta": {"kind": "html"},
        },
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(capture)
    db_session.commit()

    view = snippets.capture_content_view(db_session, capture.id, needle="15.00")
    assert view["content_available"] is True
    assert view["snippet"] is not None
    assert "15.00" in view["snippet"]
    assert "MPR" in view["snippet"]

    snip = snippets.snippet_for_observation(
        db_session, capture_id=capture.id, value="15.00"
    )
    assert snip["snippet"] is not None


def test_missing_inline_content_reports_omitted(db_session: Session) -> None:
    capture = DeskSourceCapture(
        source_key="bog_sefd_pdf",
        as_of_date=date(2026, 8, 7),
        source_url="https://example.test/sefd.pdf",
        content_sha256="b" * 64,
        payload={
            "meta": {"kind": "publication_pdf"},
            "content_omitted": "exceeds inline cap; sha256 retained",
        },
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(capture)
    db_session.commit()
    view = snippets.capture_content_view(db_session, capture.id, needle="1.23")
    assert view["content_available"] is False
    assert view["snippet"] is None


def _inline_capture(
    db: Session, raw: bytes, *, source_key: str = "bog_mpr"
) -> DeskSourceCapture:
    capture = DeskSourceCapture(
        source_key=source_key,
        as_of_date=date(2026, 8, 7),
        source_url="https://example.test/mpr",
        content_sha256=hashlib.sha256(raw).hexdigest(),
        payload={
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "meta": {"kind": "html"},
        },
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db.add(capture)
    db.commit()
    return capture


def test_deferred_payload_reads_the_original_bytes(db_session: Session) -> None:
    """A capture that defers its payload is transparent to every reader: the
    content view returns the anchor's bytes, byte for byte, and still names
    the row that holds them."""
    html = b"<html><body><table><tr><td>MPR</td><td>15.00</td></tr></table></body></html>"
    anchor = _inline_capture(db_session, html)

    deferred = DeskSourceCapture(
        source_key="bog_mpr",
        as_of_date=date(2026, 8, 8),
        source_url="https://example.test/mpr",
        content_sha256=hashlib.sha256(html).hexdigest(),
        payload=snippets.deferred_payload(anchor_id=anchor.id, meta={"kind": "html"}),
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(deferred)
    db_session.commit()

    # The row states the absence AND names where the bytes are.
    payload = deferred.payload or {}
    assert "content_base64" not in payload
    assert payload["content_deferred_to"] == str(anchor.id)
    assert str(anchor.id) in payload["content_omitted"]

    view = snippets.capture_content_view(db_session, deferred.id, needle="15.00")
    anchor_view = snippets.capture_content_view(db_session, anchor.id, needle="15.00")
    assert view["content_available"] is True
    assert view["content_bytes"] == len(html)
    assert view["text"] == anchor_view["text"]
    assert view["snippet"] == anchor_view["snippet"]
    assert view["content_deferred_to"] == str(anchor.id)
    # Content resolved, so no "missing content" banner is raised at the reader.
    assert view["content_omitted"] is None

    snip = snippets.snippet_for_observation(
        db_session, capture_id=deferred.id, value="15.00"
    )
    assert snip["snippet"] is not None
    assert snip["content_deferred_to"] == str(anchor.id)
    assert snip["hint"] is None


def test_deferred_payload_whose_anchor_is_gone_states_the_gap(db_session: Session) -> None:
    """Never invent content: an unresolvable pointer reports absence and
    names the row it looked for, rather than reading as an empty artifact."""
    missing = new_uuid7()
    orphan = DeskSourceCapture(
        source_key="bog_mpr",
        as_of_date=date(2026, 8, 8),
        source_url="https://example.test/mpr",
        content_sha256="c" * 64,
        payload=snippets.deferred_payload(anchor_id=missing, meta={"kind": "html"}),
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(orphan)
    db_session.commit()

    view = snippets.capture_content_view(db_session, orphan.id)
    assert view["content_available"] is False
    assert view["content_bytes"] == 0
    assert view["text"] is None
    assert view["content_deferred_to"] == str(missing)
    assert str(missing) in view["content_omitted"]

    snip = snippets.snippet_for_observation(db_session, capture_id=orphan.id, value="15.00")
    assert snip["snippet"] is None
    assert snip["hint"] is not None and str(missing) in snip["hint"]


def test_find_payload_anchor_is_scoped_to_the_source(db_session: Session) -> None:
    """Identical bytes under a DIFFERENT source are not an anchor — lineage
    from an observation must resolve inside its own source."""
    raw = b"<html>same bytes, different site</html>"
    other = _inline_capture(db_session, raw, source_key="gfim_daily")
    assert (
        snippets.find_payload_anchor(
            db_session,
            source_key="bog_mpr",
            content=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )
        is None
    )
    assert (
        snippets.find_payload_anchor(
            db_session,
            source_key="gfim_daily",
            content=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )
        == other.id
    )


def test_find_payload_anchor_rejects_a_digest_that_does_not_match_the_bytes(
    db_session: Session,
) -> None:
    """The digest is only the index probe. A row whose stored SHA-256 does not
    describe its bytes must never be handed back as the place those bytes
    live — deferral is confirmed on the bytes themselves."""
    lying = DeskSourceCapture(
        source_key="bog_mpr",
        as_of_date=date(2026, 8, 7),
        content_sha256=hashlib.sha256(b"claimed").hexdigest(),
        payload={
            "content_base64": base64.b64encode(b"actually something else").decode("ascii"),
            "meta": {},
        },
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(lying)
    db_session.commit()

    assert (
        snippets.find_payload_anchor(
            db_session,
            source_key="bog_mpr",
            content=b"claimed",
            content_sha256=hashlib.sha256(b"claimed").hexdigest(),
        )
        is None
    )


def test_find_payload_anchor_never_points_at_a_payload_less_row(
    db_session: Session,
) -> None:
    """An over-cap capture holds no bytes, so it can never be an anchor —
    that is what keeps every deferral resolvable."""
    over_cap = DeskSourceCapture(
        source_key="bog_sefd_pdf",
        as_of_date=date(2026, 8, 7),
        content_sha256=hashlib.sha256(b"%PDF-huge").hexdigest(),
        payload={"meta": {}, "content_omitted": "exceeds inline cap; sha256 retained"},
        parser_version="test",
        status="parsed",
        created_by="test@aequoros.com",
    )
    db_session.add(over_cap)
    db_session.commit()

    assert (
        snippets.find_payload_anchor(
            db_session,
            source_key="bog_sefd_pdf",
            content=b"%PDF-huge",
            content_sha256=hashlib.sha256(b"%PDF-huge").hexdigest(),
        )
        is None
    )
