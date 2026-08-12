"""Capture content decoding and value snippets."""

from __future__ import annotations

import base64
from datetime import date

import pytest
from sqlalchemy.orm import Session

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
