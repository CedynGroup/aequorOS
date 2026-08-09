"""Shared plumbing for the market-desk source-layer suite.

Every parser test runs against the REAL captured documents in
``tests/fixtures/market_desk/raw`` (2026-08-09 harvest; nothing synthetic
except the tiny payloads that reproduce documented dirty-data quirks the
first-page fixtures happen not to contain).
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.ids import new_uuid7
from app.models import DeskSourceCapture

FIXTURES = Path(__file__).parents[2] / "fixtures" / "market_desk"
RAW = FIXTURES / "raw"

DESK_OPERATOR = "desk-analyst@aequoros.com"


def read_fixture(name: str) -> bytes:
    return (RAW / name).read_bytes()


@pytest.fixture
def make_capture(db_session: Session):
    """Persist a DeskSourceCapture the way the capture client would."""

    def _make(
        *,
        source_key: str,
        raw: bytes,
        as_of_date: date = date(2026, 8, 9),
        source_url: str | None = None,
    ) -> DeskSourceCapture:
        capture = DeskSourceCapture(
            id=new_uuid7(),
            source_key=source_key,
            as_of_date=as_of_date,
            source_url=source_url,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            parser_version="pending",
            status="captured",
            created_by=DESK_OPERATOR,
        )
        db_session.add(capture)
        db_session.flush()
        return capture

    return _make
