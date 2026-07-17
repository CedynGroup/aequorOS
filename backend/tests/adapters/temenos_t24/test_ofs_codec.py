"""OFS codec contract: request framing, and response decoding with @FM/@VM/@SM
multivalue/subvalue flattening. Marker bytes never survive into extractor-
facing structures."""

from __future__ import annotations

import pytest

from app.adapters.temenos_t24.ofs import (
    FM,
    SM,
    VM,
    OfsError,
    build_enquiry_message,
    build_ofs_message,
    flatten_value,
    parse_ofs_response,
)

# --- multivalue flattening -------------------------------------------------

def test_flatten_scalar_returns_string() -> None:
    assert flatten_value("GHS") == "GHS"


def test_flatten_value_markers_return_list() -> None:
    assert flatten_value(f"10{VM}20{VM}30") == ["10", "20", "30"]


def test_flatten_subvalue_markers_return_list_of_lists() -> None:
    assert flatten_value(f"A{SM}B") == [["A", "B"]]
    assert flatten_value(f"A{SM}B{VM}C{SM}D") == [["A", "B"], ["C", "D"]]


def test_flatten_accepts_textual_marker_mnemonics() -> None:
    assert flatten_value("10@VM20") == ["10", "20"]
    assert flatten_value("A@SMB") == [["A", "B"]]


def test_flatten_accepts_native_t24_marker_codepoints() -> None:
    assert flatten_value("10\xfd20") == ["10", "20"]  # real VM (0xFD)
    assert flatten_value("A\xfcB") == [["A", "B"]]  # real SM (0xFC)


# --- request framing -------------------------------------------------------

def test_build_ofs_message_frames_app_id_and_fields() -> None:
    msg = build_ofs_message(
        "AA.ARRANGEMENT", "AA123", {"CUSTOMER": "C100", "AMOUNT": 5000},
        function="I", user="SVC", password="pw",
    )
    parts = msg.split(",")
    assert parts[0] == "AA.ARRANGEMENT"
    assert "SVC/pw" in parts
    assert "AA123" in parts
    assert "CUSTOMER:1:1=C100" in msg
    assert "AMOUNT:1:1=5000" in msg


def test_build_enquiry_message_defaults_to_equality() -> None:
    msg = build_enquiry_message("AEQ.CUSTOMER.MASTER", {"CO.CODE": "GH0010001"}, user="SVC")
    assert msg.startswith("ENQUIRY.SELECT,,SVC,AEQ.CUSTOMER.MASTER")
    assert "CO.CODE:EQ:GH0010001" in msg


def test_build_enquiry_message_supports_operand_tuples() -> None:
    msg = build_enquiry_message(
        "AEQ.AA.LENDING.POSITIONS", [("MATURITY.DATE", "GT", "20260101")]
    )
    assert "MATURITY.DATE:GT:20260101" in msg


# --- response decoding -----------------------------------------------------

def test_parse_single_record_success() -> None:
    raw = "AA123/1,CUSTOMER:1:1=C100,CURRENCY:1:1=GHS,AMOUNT:1:1=5000"
    resp = parse_ofs_response(raw)
    assert resp.ok
    assert resp.record is not None
    assert resp.record.record_id == "AA123"
    assert resp.record.scalar("CUSTOMER") == "C100"
    assert resp.record.scalar("AMOUNT") == "5000"


def test_parse_multi_record_split_on_field_marker() -> None:
    raw = f"AA123/1,CUSTOMER:1:1=C100{FM}AA124/1,CUSTOMER:1:1=C200"
    resp = parse_ofs_response(raw)
    assert resp.ok
    assert [r.record_id for r in resp.records] == ["AA123", "AA124"]
    assert resp.records[1].scalar("CUSTOMER") == "C200"


def test_parse_reassembles_multivalue_positions() -> None:
    raw = "AA123/1,CHARGE:1:1=10,CHARGE:2:1=20,CHARGE:3:1=30"
    resp = parse_ofs_response(raw)
    assert resp.record is not None
    assert resp.record.fields["CHARGE"] == ["10", "20", "30"]


def test_parse_reassembles_subvalue_positions() -> None:
    raw = "AA123/1,SCHED:1:1=DUE,SCHED:1:2=2026-01-01"
    resp = parse_ofs_response(raw)
    assert resp.record is not None
    assert resp.record.fields["SCHED"] == [["DUE", "2026-01-01"]]


def test_scalar_takes_first_of_multivalue() -> None:
    raw = "AA123/1,GUARANTOR:1:1=G1,GUARANTOR:2:1=G2"
    resp = parse_ofs_response(raw)
    assert resp.record is not None
    assert resp.record.scalar("GUARANTOR") == "G1"


def test_error_response_is_not_ok_and_carries_no_records() -> None:
    resp = parse_ofs_response("AA123/-1,CUSTOMER field is mandatory")
    assert not resp.ok
    assert resp.records == ()
    assert resp.error_code == "-1"
    assert resp.error_text == "CUSTOMER field is mandatory"


def test_error_response_without_record_id() -> None:
    resp = parse_ofs_response("/-1,SECURITY.VIOLATION")
    assert not resp.ok
    assert resp.error_code == "-1"


def test_empty_response_raises_ofs_error() -> None:
    with pytest.raises(OfsError):
        parse_ofs_response("   ")


def test_no_marker_bytes_survive_into_parsed_fields() -> None:
    raw = "AA123/1,SCHED:1:1=A,SCHED:1:2=B,CHARGE:1:1=10,CHARGE:2:1=20"
    resp = parse_ofs_response(raw)
    assert resp.record is not None
    flat = repr(resp.record.fields)
    for marker in (FM, VM, SM, "\xfe", "\xfd", "\xfc"):
        assert marker not in flat
