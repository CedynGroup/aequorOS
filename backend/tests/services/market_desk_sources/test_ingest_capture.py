"""End-to-end: real fixture bytes -> ingest_capture -> DeskObservation rows,
with append-only supersession on re-ingest and honest capture stamping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeskObservation
from app.services.market_desk import observations
from app.services.market_desk.sources import ingest_capture
from app.services.market_desk.sources.core import (
    QF_CROSS_SOURCE_MISMATCH,
    QF_STALE_SOURCE,
)
from tests.services.market_desk_sources.conftest import DESK_OPERATOR, read_fixture


def _current(db: Session, series_code: str, as_of: date) -> list[DeskObservation]:
    return list(
        db.scalars(
            select(DeskObservation).where(
                DeskObservation.series_code == series_code,
                DeskObservation.as_of_date == as_of,
                DeskObservation.superseded_by.is_(None),
            )
        )
    )


class TestIngestCapture:
    def test_fixture_to_observations_in_db(self, db_session: Session, make_capture) -> None:
        raw = read_fixture("bog_wdt_table69_interbank_daily_page.json")
        capture = make_capture(source_key="bog_interbank_daily", raw=raw)
        result = ingest_capture(db_session, capture, raw)

        assert result.errors == []
        assert capture.status == "parsed"
        assert capture.parser_version == "bog_wdt/1"
        rows = _current(db_session, "GHS.INTERBANK.ON", date(2026, 8, 7))
        assert len(rows) == 1
        assert rows[0].value == Decimal("10.23")
        assert rows[0].capture_id == capture.id
        assert rows[0].entered_by is None  # capture-linked, not manual

    def test_reingest_supersedes_current_generation(
        self, db_session: Session, make_capture
    ) -> None:
        raw = read_fixture("bog_wdt_table69_interbank_daily_page.json")
        first = make_capture(source_key="bog_interbank_daily", raw=raw)
        ingest_capture(db_session, first, raw)
        old_row = _current(db_session, "GHS.INTERBANK.ON", date(2026, 8, 7))[0]

        second = make_capture(source_key="bog_interbank_daily", raw=raw)
        ingest_capture(db_session, second, raw)

        current = _current(db_session, "GHS.INTERBANK.ON", date(2026, 8, 7))
        assert len(current) == 1
        assert current[0].capture_id == second.id
        db_session.refresh(old_row)
        assert old_row.superseded_by == current[0].id  # append-only: old row stays

    def test_parse_failure_stamps_capture_failed(
        self, db_session: Session, make_capture
    ) -> None:
        raw = b"this is not json"
        capture = make_capture(source_key="bog_interbank_daily", raw=raw)
        result = ingest_capture(db_session, capture, raw)
        assert capture.status == "failed"
        assert capture.parse_error
        assert result.errors
        assert db_session.scalars(select(DeskObservation)).all() == []

    def test_unregistered_source_key_fails_honestly(
        self, db_session: Session, make_capture
    ) -> None:
        capture = make_capture(source_key="mystery_source", raw=b"{}")
        result = ingest_capture(db_session, capture, b"{}")
        assert capture.status == "failed"
        assert "no parser registered" in (capture.parse_error or "")
        assert result.errors

    def test_stale_source_flag_rides_into_db(
        self, db_session: Session, make_capture
    ) -> None:
        # GSS statsbank ends 2024M07; captured 2026-08-09 (README quirk 5).
        raw = read_fixture("gss_pxweb_interest_px_data_response.json")
        capture = make_capture(source_key="gss_interest_px", raw=raw)
        result = ingest_capture(db_session, capture, raw)
        assert capture.status == "parsed"
        assert any("stale" in w for w in result.warnings)
        newest = _current(db_session, "GHS.GRR", date(2024, 7, 1))
        assert len(newest) == 1
        assert QF_STALE_SOURCE in newest[0].quality_flags

    def test_manual_entry_supersedes_and_is_superseded_symmetrically(
        self, db_session: Session, make_capture
    ) -> None:
        # Manual fallback is a first-class observation: a desk correction
        # supersedes the parsed row, and a later re-parse supersedes it back.
        raw = read_fixture("bog_wdt_table69_interbank_daily_page.json")
        capture = make_capture(source_key="bog_interbank_daily", raw=raw)
        ingest_capture(db_session, capture, raw)

        manual = observations.record_manual_observation(
            db_session,
            series_code="GHS.INTERBANK.ON",
            as_of_date=date(2026, 8, 7),
            value=Decimal("10.25"),
            unit="pct",
            entered_by=DESK_OPERATOR,
        )
        current = _current(db_session, "GHS.INTERBANK.ON", date(2026, 8, 7))
        assert [row.id for row in current] == [manual.id]
        assert current[0].entered_by == DESK_OPERATOR

    def test_cross_source_reconciliation_flags_disagreement(
        self, db_session: Session, make_capture
    ) -> None:
        # Seed a desk GRR value that disagrees with what GSS will publish
        # for the same month (29.4 in the fixture; tolerance is 0.5pp).
        observations.record_manual_observation(
            db_session,
            series_code="GHS.SEFD.GRR",
            as_of_date=date(2024, 7, 1),
            value=Decimal("31.0"),
            unit="pct",
            entered_by=DESK_OPERATOR,
        )
        raw = read_fixture("gss_pxweb_interest_px_data_response.json")
        capture = make_capture(source_key="gss_interest_px", raw=raw)
        result = ingest_capture(db_session, capture, raw)

        assert any("cross-source mismatch" in w for w in result.warnings)
        gss_row = _current(db_session, "GHS.GRR", date(2024, 7, 1))[0]
        sefd_row = _current(db_session, "GHS.SEFD.GRR", date(2024, 7, 1))[0]
        for row in (gss_row, sefd_row):
            assert QF_CROSS_SOURCE_MISMATCH in row.quality_flags
            assert row.attributes["cross_source_mismatch"]["tolerance"] == "0.5"

    def test_gfim_workbook_end_to_end(self, db_session: Session, make_capture) -> None:
        raw = read_fixture("gfim_daily_trading_report_2026-08-07.xlsx")
        capture = make_capture(
            source_key="gfim_daily_xlsx", raw=raw, as_of_date=date(2026, 8, 7)
        )
        result = ingest_capture(db_session, capture, raw)
        assert result.errors == []
        assert capture.status == "parsed"
        bill = _current(db_session, "GHS.GFIM.GHGGOGI01669.YIELD", date(2026, 8, 7))
        assert len(bill) == 1
        # 15dp published yield quantized into Numeric(28, 10).
        assert bill[0].value == Decimal("9.9870774713")
