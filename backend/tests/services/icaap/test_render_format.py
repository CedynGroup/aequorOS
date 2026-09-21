"""Display formatting for ICAAP documents.

The tests here are about honesty rather than prettiness. An ICAAP is read by a
Board and by a supervisor, so the two failures that matter are a gap printed as
a number and a number printed as something it is not: a missing figure shown as
``0``, a stored enum shown raw, a scale applied silently, a total invented
because the payload had none.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.icaap.document import NOT_AVAILABLE
from app.services.icaap.render import format as fmt


class TestScalarValues:
    @pytest.mark.parametrize(
        ("value", "kind", "expected"),
        [
            ("1234567.00", "amount", "1,234,567.00"),
            ("-2500.5", "amount", "(2,500.50)"),
            ("0", "amount", "0.00"),
            ("15.4249", "ratio_pct", "15.42%"),
            ("-3", "ratio_pct", "(3.00)%"),
            ("12", "count", "12"),
            ("1200", "count", "1,200"),
            ("3", "years", "3 years"),
            ("1", "years", "1 year"),
            ("1.4", "multiplier", "1.40×"),
            ("true", "boolean", "Yes"),
            ("false", "boolean", "No"),
            ("2025-12-31", "date", "31 Dec 2025"),
            ("baseline", "text", "baseline"),
        ],
    )
    def test_each_kind_prints_in_its_own_convention(
        self, value: str, kind: str, expected: str
    ) -> None:
        assert fmt.format_value(value, kind=kind) == expected

    def test_a_missing_value_is_named_not_shown_as_zero(self) -> None:
        # The whole point: a bank that has not reported an exposure and a bank
        # that has reported zero exposure must not read the same.
        assert fmt.format_value(None, kind="amount") == NOT_AVAILABLE
        assert fmt.format_value("", kind="ratio_pct") == NOT_AVAILABLE
        assert fmt.format_value("   ", kind="count") == NOT_AVAILABLE

    def test_an_unparseable_number_prints_as_stored(self) -> None:
        # Never silently dropped and never coerced: the operator has to be able
        # to see what the resolver actually captured.
        assert fmt.format_value("n/a", kind="amount") == "n/a"
        assert fmt.format_value("2025-13-45", kind="date") == "2025-13-45"
        assert fmt.format_value("maybe", kind="boolean") == "maybe"

    def test_amounts_carry_their_currency_only_when_asked(self) -> None:
        assert fmt.format_amount("1000", currency="GHS") == "GHS 1,000.00"
        assert fmt.format_amount("1000") == "1,000.00"

    def test_dates_accept_a_date_object(self) -> None:
        assert fmt.format_date(date(2026, 1, 5)) == "5 Jan 2026"


class TestFacts:
    def test_a_fact_with_no_value_is_not_available(self) -> None:
        fact = {"label": "Total capital", "kind": "amount", "value": None, "currency": "GHS"}
        assert fmt.format_fact(fact) == NOT_AVAILABLE

    def test_an_absent_fact_is_not_available(self) -> None:
        assert fmt.format_fact(None) == NOT_AVAILABLE
        assert fmt.format_fact({}) == NOT_AVAILABLE

    def test_an_amount_falls_back_to_the_documents_currency(self) -> None:
        fact = {"kind": "amount", "value": "12", "currency": None}
        assert fmt.format_fact(fact, fallback_currency="NGN") == "NGN 12.00"

    def test_a_non_amount_never_acquires_a_currency(self) -> None:
        fact = {"kind": "ratio_pct", "value": "9"}
        assert fmt.format_fact(fact, fallback_currency="NGN") == "9.00%"

    def test_the_map_is_keyed_by_block_and_fact(self) -> None:
        resolved = fmt.fact_text_map(
            {"b1": {"car_pct": {"kind": "ratio_pct", "value": "15.4"}, "gap": None}},
            fallback_currency="GHS",
        )
        assert resolved[("b1", "car_pct")] == "15.40%"
        assert resolved[("b1", "gap")] == NOT_AVAILABLE


class TestLabels:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("solo", "Solo"), ("consolidated", "Consolidated")],
    )
    def test_basis_prints_as_words(self, raw: str, expected: str) -> None:
        assert fmt.basis_label(raw) == expected

    def test_an_unmapped_value_is_still_readable(self) -> None:
        # A raw enum on a document the Board reads is a defect the platform has
        # already been pulled up on, so the fallback is words, not the key.
        assert fmt.cycle_kind_label("some_new_kind") == "Some new kind"
        assert fmt.block_status_label("source_withdrawn") == "Source withdrawn"
        assert "_" not in fmt.requirement_status_label("not_applicable")

    def test_a_stale_block_always_carries_a_note(self) -> None:
        for status in ("unbound", "stale", "as_of_mismatch", "source_withdrawn", "source_missing"):
            note = fmt.block_status_note(status)
            assert note and note[0].isupper()

    def test_a_current_block_carries_no_note(self) -> None:
        assert fmt.block_status_note("fresh") is None

    def test_a_pin_prints_its_reason(self) -> None:
        note = fmt.block_status_note("pinned", pin_reason="Board reviewed these figures")
        assert note is not None
        assert "Board reviewed these figures" in note

    def test_a_pin_without_a_reason_still_says_it_is_pinned(self) -> None:
        note = fmt.block_status_note("pinned", pin_reason=None)
        assert note is not None
        assert "Pinned" in note

    def test_content_label_names_the_committed_version(self) -> None:
        assert fmt.content_label(committed_version=3) == "Version 3 (committed)"
        assert fmt.content_label(committed_version=None) == "Working draft — not committed"

    def test_the_pending_text_note_names_the_regulator_from_data(self) -> None:
        # Jurisdiction is data: the short form is passed in, never written here.
        assert "CBN" in fmt.pending_primary_text_note("CBN")
        assert "BoG" not in fmt.pending_primary_text_note("CBN")


class TestUnits:
    def test_the_scale_is_stated_not_applied(self) -> None:
        assert fmt.unit_note({"currency": "GHS", "scale": 1000}) == "Amounts in GHS thousands."
        assert fmt.unit_note({"currency": "GHS", "scale": 1}) == "Amounts in GHS."

    def test_an_unknown_scale_is_spelled_out_rather_than_guessed(self) -> None:
        note = fmt.unit_note({"currency": "NGN", "scale": 25})
        assert note is not None
        assert "25" in note

    def test_no_currency_means_no_claim(self) -> None:
        assert fmt.unit_note(None) is None
        assert fmt.unit_note({"scale": 1000}) is None


class TestBlockRender:
    def _payload(self) -> dict[str, object]:
        return {
            "schema": fmt.PAYLOAD_SCHEMA,
            "title": "Capital position",
            "as_of": "2025-12-31",
            "source_label": "Official capital run (baseline)",
            "unit": {"currency": "GHS", "scale": 1},
            "tables": [
                {
                    "key": "capital_components",
                    "title": "Capital components",
                    "columns": [
                        {"key": "label", "label": "Component", "kind": "text"},
                        {"key": "amount", "label": "Amount", "kind": "amount"},
                    ],
                    "rows": [
                        {"cells": {"label": "CET1 capital", "amount": "1234567"}},
                        {"cells": {"label": "Total capital", "amount": "-25"}, "emphasis": "total"},
                        {"cells": {"label": "Not reported", "amount": None}},
                    ],
                }
            ],
            "notes": ["Sourced from the sealed run."],
        }

    def test_a_payload_becomes_one_table_with_formatted_cells(self) -> None:
        block = fmt.block_render(
            block_id="b1",
            payload=self._payload(),
            status="fresh",
            fallback_title="Block",
        )
        assert block.title == "Capital position"
        assert block.as_of == date(2025, 12, 31)
        assert len(block.tables) == 1
        table = block.tables[0]
        assert [column.label for column in table.columns] == ["Component", "Amount"]
        assert table.rows[0].cells["amount"] == "1,234,567.00"
        assert table.rows[1].cells["amount"] == "(25.00)"
        assert table.rows[1].emphasis == "total"

    def test_a_table_cell_with_no_value_stays_blank(self) -> None:
        # Inside a table the row and column already say what is missing, and a
        # column of "Not available" is unreadable — unlike a figure quoted in
        # a sentence, which does need naming.
        block = fmt.block_render(
            block_id="b1", payload=self._payload(), status="fresh", fallback_title="Block"
        )
        assert block.tables[0].rows[2].cells["amount"] == ""

    def test_no_row_is_added_that_the_payload_did_not_carry(self) -> None:
        block = fmt.block_render(
            block_id="b1", payload=self._payload(), status="fresh", fallback_title="Block"
        )
        assert len(block.tables[0].rows) == 3

    def test_an_unbound_block_still_renders_with_its_status(self) -> None:
        block = fmt.block_render(
            block_id="b2", payload=None, status="unbound", fallback_title="Concentration"
        )
        assert block.title == "Concentration"
        assert block.tables == ()
        assert block.status_note is not None

    def test_a_newer_payload_schema_is_rendered_and_flagged(self) -> None:
        payload = self._payload()
        payload["schema"] = "icaap-block-payload-v9"
        block = fmt.block_render(
            block_id="b1", payload=payload, status="fresh", fallback_title="Block"
        )
        assert block.tables
        assert any("icaap-block-payload-v9" in note for note in block.notes)

    def test_a_table_with_no_columns_does_not_raise(self) -> None:
        block = fmt.block_render(
            block_id="b1",
            payload={"tables": [{"key": "t", "title": "Empty", "columns": [], "rows": []}]},
            status="fresh",
            fallback_title="Block",
        )
        assert block.tables[0].columns == ()
        assert block.tables[0].rows == ()

    def test_malformed_table_entries_are_skipped_not_fatal(self) -> None:
        block = fmt.block_render(
            block_id="b1",
            payload={"tables": ["not-a-table", {"key": "t", "columns": None, "rows": None}]},
            status="fresh",
            fallback_title="Block",
        )
        assert len(block.tables) == 1


class TestFilenames:
    def test_the_name_says_it_is_a_draft(self) -> None:
        name = fmt.draft_filename(
            institution_short_name="Sample Bank",
            fiscal_year=2025,
            basis="solo",
            extension="pdf",
        )
        assert name == "ICAAP-Sample-Bank-FY2025-solo-DRAFT.pdf"

    @pytest.mark.parametrize(
        "hostile",
        [
            'Bank"; rm -rf /',
            "Bank\r\nX-Injected: 1",
            "../../etc/passwd",
            "Ban\x00k",
            "Ƹ Bank Ɔ",
        ],
    )
    def test_a_hostile_institution_name_cannot_escape_the_filename(self, hostile: str) -> None:
        # The name reaches a Content-Disposition header, so it is restricted to
        # a safe alphabet rather than escaped.
        name = fmt.draft_filename(
            institution_short_name=hostile, fiscal_year=2025, basis="solo", extension="docx"
        )
        assert set(name) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-.")
        assert name.endswith(".docx")

    def test_a_name_that_sanitises_to_nothing_still_produces_a_file(self) -> None:
        name = fmt.draft_filename(
            institution_short_name="///", fiscal_year=2025, basis="solo", extension="pdf"
        )
        assert name.startswith("ICAAP-institution-FY2025")

    def test_a_very_long_name_is_bounded(self) -> None:
        name = fmt.draft_filename(
            institution_short_name="A" * 5000,
            fiscal_year=2025,
            basis="consolidated",
            extension="pdf",
        )
        assert len(name) <= 130
