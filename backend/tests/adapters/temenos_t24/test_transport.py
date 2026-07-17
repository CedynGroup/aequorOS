"""Transport seam contract: the default is unavailable-not-silent, fixtures
replay recorded payloads, requests resolve placeholders, and no fault leaks
core internals to the bank-facing surface."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.adapters.temenos_t24.auth import SimulatedSessionProvider, TemenosCredentials
from app.adapters.temenos_t24.catalog import load_mode_catalog
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.errors import TemenosError, TemenosErrorCode
from app.adapters.temenos_t24.transport import (
    FixtureTransport,
    RawDomainResponse,
    UnavailableTransport,
    build_domain_request,
)

_AS_OF = date(2026, 6, 30)


def _session():
    return SimulatedSessionProvider().sign_on(
        "OFS", "ofs://sample-bank", TemenosCredentials(username="SVC.AEQUOROS"), company="GH0010001"
    )


def _request(domain: CoreBankingDomain, company: str | None = "GH0010001"):
    entry = load_mode_catalog("OFS").entries[domain]
    return build_domain_request(entry, as_of=_AS_OF, company=company)


def test_build_domain_request_fills_company_placeholder() -> None:
    request = _request(CoreBankingDomain.POSITIONS_LOANS)
    assert request.selection["CO.CODE"] == "GH0010001"
    assert request.application == "AA.ARRANGEMENT"
    assert "AMOUNT" in request.fields  # field_map keys become the select list


def test_unavailable_transport_classifies_core_unavailable() -> None:
    request = _request(CoreBankingDomain.GL_BALANCES)
    with pytest.raises(TemenosError) as excinfo:
        UnavailableTransport().fetch(_session(), request)
    assert excinfo.value.code is TemenosErrorCode.CORE_UNAVAILABLE


def test_unavailable_transport_does_not_leak_internal_detail_into_str() -> None:
    request = _request(CoreBankingDomain.GL_BALANCES)
    try:
        UnavailableTransport().fetch(_session(), request)
    except TemenosError as err:
        assert "GENERAL.LEDGER" not in str(err)
        assert "OFS" not in str(err)
        assert request.domain in err.internal_detail  # internal keeps the detail


def test_fixture_transport_replays_json_records(tmp_path: Path) -> None:
    (tmp_path / "GL_BALANCES.json").write_text(
        json.dumps({"source": "AEQ.NOFILE.GL.BALANCES", "records": [
            "1000/1,DESCRIPTION:1:1=Cash,CURRENCY:1:1=GHS,LCY.BALANCE:1:1=500000",
        ]}),
        encoding="utf-8",
    )
    request = _request(CoreBankingDomain.GL_BALANCES)
    response = FixtureTransport(tmp_path).fetch(_session(), request)
    assert response.record_count == 1
    assert response.source == "AEQ.NOFILE.GL.BALANCES"
    assert response.domain == "GL_BALANCES"


def test_fixture_transport_replays_ofs_text_one_block_per_line(tmp_path: Path) -> None:
    (tmp_path / "POSITIONS_LOANS.ofs").write_text(
        "AA1/1,AMOUNT:1:1=100\nAA2/1,AMOUNT:1:1=200\n", encoding="utf-8"
    )
    request = _request(CoreBankingDomain.POSITIONS_LOANS)
    response = FixtureTransport(tmp_path).fetch(_session(), request)
    assert response.record_count == 2


def test_fixture_transport_missing_recording_is_no_data(tmp_path: Path) -> None:
    request = _request(CoreBankingDomain.POSITIONS_LOANS)
    with pytest.raises(TemenosError) as excinfo:
        FixtureTransport(tmp_path).fetch(_session(), request)
    assert excinfo.value.code is TemenosErrorCode.NO_DATA_RETURNED


def test_raw_domain_response_round_trips_through_bundle_dict(tmp_path: Path) -> None:
    (tmp_path / "GL_BALANCES.json").write_text(
        json.dumps(["1000/1,LCY.BALANCE:1:1=1"]), encoding="utf-8"
    )
    request = _request(CoreBankingDomain.GL_BALANCES)
    response = FixtureTransport(tmp_path).fetch(_session(), request)
    clone = RawDomainResponse.from_bundle_dict(response.to_bundle_dict())
    assert clone == response


def test_request_with_no_company_leaves_placeholder_blank() -> None:
    request = _request(CoreBankingDomain.POSITIONS_LOANS, company=None)
    assert request.selection["CO.CODE"] == ""
