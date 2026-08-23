"""An official BoG return must be approvable; a missing totals block must not be.

Every ``bog_form`` snapshot writes ``"totals": []`` by design — an official BoG
return's roll-ups are the template's own formula cells, evaluated as published,
and a totals section assembled by the platform would be arithmetic BoG never
printed on the form. The package completeness rule, written for the older
snapshot shape, treated that empty block as an ERROR, and an ERROR blocks the
``validated -> pending_approval`` transition outright
(``workflow.request_approval``). All 23 registered ``bog_form`` returns were
therefore generable, exportable — and unfileable.

The fix is a discriminator, not a deletion: the completeness rule reads the
snapshot's OWN authority record (``provenance.authority == "template_formula"``,
corroborated by the committed ``template_hash`` it claims authority for) and
excuses the totals block only for a snapshot that declares one. A family that
owes headline totals and omits them still ERRORs, and the report says why in
either direction.

These tests pin both halves:

* end to end, through the real API, a BSD2 package goes generated -> validated
  -> pending_approval, and the report carries the stated substitution;
* at the rule, a snapshot with no totals and no declaration — or a declaration
  that names no template — still ERRORs and still cannot request approval.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import Bank, RegulatoryPackage
from app.schemas.regulatory_reporting import (
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services.regulatory_reporting import generation, validation, workflow
from app.services.regulatory_reporting.bog_forms.catalog import form_spec
from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.provenance import (
    ReportAuthority,
    build_template_provenance,
)
from app.services.regulatory_reporting.registry import REGISTRY, get_definition
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

PERIOD_END = date(2026, 3, 31)
#: A position-sourced official form that depends on no other form — the
#: shortest honest path from generation to an approval request.
FORM_CODE = "BSD2"
PACKAGES = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
#: ``build_template_provenance`` reads only jurisdiction/currency/type off the
#: bank (docs: jurisdiction is data), so the digest sweep needs no DB row.
_STUB_BANK = SimpleNamespace(jurisdiction_code="GH", currency="GHS", institution_type="bank")


# ---------------------------------------------------------------------------
# end to end: the blocker is gone
# ---------------------------------------------------------------------------


def _materialize() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def test_a_bog_return_reaches_approval_requested(db_client: TestClient) -> None:
    """generated -> validated -> pending_approval, over the production routes.

    This is the whole point: before the discriminator existed this test could
    not be written, because ``validate`` left every BSD package in ``generated``
    with one ERROR and ``request-approval`` answered 409.
    """
    _materialize()

    created = db_client.post(
        PACKAGES,
        headers=headers(),
        json={"return_code": FORM_CODE, "reporting_date": PERIOD_END.isoformat()},
    )
    assert created.status_code == 201, f"{created.status_code} {created.text[:400]}"
    package = created.json()
    package_id = package["id"]
    assert package["status"] == "generated"
    # The snapshot under test really is the empty-totals shape.
    assert package["snapshot"]["totals"] == []

    validated = db_client.post(f"{PACKAGES}/{package_id}/validate", headers=headers())
    assert validated.status_code == 200, f"{validated.status_code} {validated.text[:400]}"
    body = validated.json()
    report = body["validation_report"]
    assert report["error_count"] == 0, report["findings"]
    assert report["passed"] is True
    assert body["status"] == "validated"

    # The control is satisfied differently and SAYS so — an examiner reading the
    # report learns why there is no totals block, and which workbook stands in
    # its place, without reading the code.
    stated = [
        finding
        for finding in report["findings"]
        if finding["rule"] == validation.COMPLETENESS_RULE
        and "No separate 'totals' block is required" in finding["detail"]
    ]
    assert len(stated) == 1, report["findings"]
    assert stated[0]["severity"] == "INFO"
    assert package["snapshot"]["provenance"]["official_workbook"] in stated[0]["detail"]

    requested = db_client.post(
        f"{PACKAGES}/{package_id}/request-approval", headers=headers(), json={}
    )
    assert requested.status_code == 200, f"{requested.status_code} {requested.text[:400]}"
    assert requested.json()["status"] == "pending_approval"


def test_every_bog_return_validates_clean_not_only_the_one_above(db_session: Session) -> None:
    """No OTHER ERROR blocks the same transition, proved return by return.

    Unblocking one form proves one form. This generates all 23 over the
    canonical book and asserts each validation report carries zero ERRORs — so
    the claim "the BSD family can now be approved" is measured rather than
    inferred from BSD2. It also pins the two structural reasons no other rule
    can ERROR on a BoG snapshot: every sheet section is ``optional`` (an empty
    sheet is INFO, not ERROR) and every section carries ``total: None`` (the
    cross-foot rule has nothing to declare), so a regression in either would
    surface here rather than at an approver's desk.
    """
    materialize_canonical_test_book(db_session)
    codes = sorted(code for code, d in REGISTRY.items() if d.generator == "bog_form")
    reports: dict[str, list[str]] = {}
    for code in codes:
        package = generation.generate_package(
            db_session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code=code, reporting_date=PERIOD_END),
        )
        assert package.snapshot["totals"] == [], code
        assert all(section["optional"] for section in package.snapshot["sections"]), code
        assert all(section["total"] is None for section in package.snapshot["sections"]), code

        validated = validation.validate_package(db_session, CTX, SAMPLE_BANK_ID, package.id)
        assert validated.validation_report is not None
        assert validated.status == "validated", (code, validated.validation_report.findings)
        reports[code] = [
            f.detail for f in validated.validation_report.findings if f.severity == "ERROR"
        ]
    assert len(reports) == 23, sorted(reports)  # noqa: PLR2004 — BSD1 … BSD17
    assert not {code: errors for code, errors in reports.items() if errors}, reports


def test_the_declaration_is_available_to_every_registered_bog_return() -> None:
    """All 23 ``bog_form`` entries — not just the one generated above.

    Generating all of them here would duplicate
    ``test_phase2_full_report_proof``; what this asserts is narrower and is the
    thing the fix turns on: the authority record each of them will carry is
    accepted by the discriminator, so none is left behind. Six of these layouts
    (BSD11, BSD14, BSD15A, BSD15B, BSD17, BSD2A) carry no formula cell at all —
    BoG publishes them as blank grids — which is why the corroboration is the
    committed template digest and never a formula count.
    """
    codes = sorted(code for code, d in REGISTRY.items() if d.generator == "bog_form")
    assert len(codes) == 23, codes  # noqa: PLR2004 — BSD1 … BSD17, 23 registered returns
    for code in codes:
        provenance = build_template_provenance(
            definition=REGISTRY[code],
            bank=_STUB_BANK,
            effective_date=PERIOD_END,
            form_code=code,
            workbook=form_spec(code).workbook,
            authority_counts={},
            formula_cells_evaluated=sum(
                len(sheet.formula_cells) for sheet in load_layout(code).sheets
            ),
        ).to_dict()
        snapshot = {"totals": [], "provenance": provenance}
        assert validation._template_authoritative_rollups(snapshot) is not None, code  # noqa: SLF001
        assert not _totals_errors(snapshot), code


# ---------------------------------------------------------------------------
# the companion: a return that genuinely lacks its totals still errors
# ---------------------------------------------------------------------------


def _bank(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Totals authority tenant",
        short_name="Tot",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _package(
    db: Session,
    bank: Bank,
    *,
    totals: list[dict[str, Any]],
    provenance: dict[str, Any] | None,
) -> RegulatoryPackage:
    snapshot: dict[str, Any] = {
        "schema_version": "regulatory-package-v1",
        "reporting_date": "2026-03-31",
        "institution": {"bank_id": bank.id, "name": bank.name},
        "sections": [{"code": "s1", "title": "S1", "rows": [{"code": "r", "value": "1"}]}],
        "totals": totals,
        "metadata": {"generated_at": "2026-03-31T00:00:00+00:00"},
    }
    if provenance is not None:
        snapshot["provenance"] = provenance
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=bank.id,
        return_family="liquidity",
        return_code="TEST-TOTALS",
        reporting_date=date(2026, 3, 31),
        frequency="monthly",
        basis="solo",
        status="generated",
        version=1,
        snapshot=snapshot,
        source_runs=[],
        generated_by=USER_1,
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    db.flush()
    return package


def _totals_errors(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    return [
        finding
        for finding in validation._completeness_findings(snapshot)  # noqa: SLF001
        if finding["severity"] == "ERROR" and "'totals' block" in finding["detail"]
    ]


def _engine_run_provenance() -> dict[str, Any]:
    """What a calculation-owned family declares: a run owns the figures."""
    return {
        "authority": ReportAuthority.ENGINE_RUN.value,
        "template_hash": "",
        "official_workbook": "",
    }


def test_a_family_that_owes_headline_totals_still_errors_without_them() -> None:
    """The control this fix must not blind."""
    errors = _totals_errors(
        {"reporting_date": "2026-03-31", "institution": {}, "sections": [], "totals": []}
    )
    assert len(errors) == 1, errors
    # The ERROR names the only honest way out, so it cannot be read as a nag.
    assert ReportAuthority.TEMPLATE_FORMULA.value in errors[0]["detail"]

    with_engine_run = _totals_errors(
        {
            "reporting_date": "2026-03-31",
            "institution": {},
            "sections": [],
            "totals": [],
            "provenance": _engine_run_provenance(),
        }
    )
    assert len(with_engine_run) == 1, with_engine_run


def test_a_declaration_that_names_no_template_is_not_believed() -> None:
    """The authority string alone is not a free pass.

    A snapshot claiming template authority must name the committed layout it
    claims it for; otherwise the claim is unfalsifiable and the block is just
    missing.
    """
    bare = {
        "reporting_date": "2026-03-31",
        "institution": {},
        "sections": [],
        "totals": [],
        "provenance": {"authority": ReportAuthority.TEMPLATE_FORMULA.value, "template_hash": ""},
    }
    assert validation._template_authoritative_rollups(bare) is None  # noqa: SLF001
    assert len(_totals_errors(bare)) == 1


def test_a_snapshot_that_carries_totals_is_untouched() -> None:
    """The generic families keep validating exactly as before."""
    populated = {
        "reporting_date": "2026-03-31",
        "institution": {},
        "sections": [],
        "totals": [{"code": "hqla_total_ghs", "value": "1"}],
        "provenance": _engine_run_provenance(),
    }
    assert not _totals_errors(populated)
    assert not [
        f
        for f in validation._completeness_findings(populated)  # noqa: SLF001
        if "No separate 'totals' block is required" in f["detail"]
    ]


def test_an_undeclared_empty_totals_block_still_blocks_the_approval_request(
    db_session: Session,
) -> None:
    """End of the same chain: no declaration -> ERROR -> 409 on request-approval."""
    bank = _bank(db_session)
    package = _package(db_session, bank, totals=[], provenance=_engine_run_provenance())
    db_session.commit()

    result = validation.validate_package(db_session, CTX, bank.id, package.id)
    assert result.status == "generated"
    assert result.validation_report is not None
    assert result.validation_report.passed is False
    assert result.validation_report.error_count >= 1

    with pytest.raises(HTTPException) as exc_info:
        workflow.request_approval(
            db_session, CTX, bank.id, package.id, PackageApprovalRequestCreate()
        )
    assert exc_info.value.status_code == 409  # noqa: PLR2004 — HTTP 409 Conflict


def test_a_declared_template_authoritative_package_validates_and_can_be_requested(
    db_session: Session,
) -> None:
    """The same row, with the declaration, clears validation and transitions.

    Same shape, same empty ``totals`` — only the snapshot's stated authority
    differs. That isolates the discriminator from everything else the pipeline
    checks.
    """
    bank = _bank(db_session)
    package = _package(
        db_session,
        bank,
        totals=[],
        provenance={
            "authority": ReportAuthority.TEMPLATE_FORMULA.value,
            "template_hash": "0" * 64,
            "official_workbook": "BSD2 BALANCE SHEET.xlsx",
            "formula_cells_evaluated": 536,
        },
    )
    db_session.commit()

    result = validation.validate_package(db_session, CTX, bank.id, package.id)
    assert result.validation_report is not None
    assert result.validation_report.error_count == 0, result.validation_report.findings
    assert result.status == "validated"

    requested = workflow.request_approval(
        db_session, CTX, bank.id, package.id, PackageApprovalRequestCreate()
    )
    assert requested.status == "pending_approval"


def test_the_stated_substitution_names_the_workbook_and_the_evaluated_cells(
    db_session: Session,
) -> None:
    """The INFO line is evidence, not a placeholder."""
    bank = _bank(db_session)
    package = _package(
        db_session,
        bank,
        totals=[],
        provenance={
            "authority": ReportAuthority.TEMPLATE_FORMULA.value,
            "template_hash": "abcdef0123456789" + "0" * 48,
            "official_workbook": "BSD2 BALANCE SHEET.xlsx",
            "formula_cells_evaluated": 536,
        },
    )
    db_session.commit()

    result = validation.validate_package(db_session, CTX, bank.id, package.id)
    assert result.validation_report is not None
    stated = [
        finding
        for finding in result.validation_report.findings
        if "No separate 'totals' block is required" in finding.detail
    ]
    assert len(stated) == 1, result.validation_report.findings
    detail = stated[0].detail
    assert "BSD2 BALANCE SHEET.xlsx" in detail
    assert "abcdef012345" in detail
    assert "536 of the template's own formula cells" in detail


def test_the_declaration_is_read_off_the_sealed_snapshot_not_the_registry(
    db_session: Session,
) -> None:
    """Why the discriminator lives on the snapshot rather than on the registry.

    ``TEST-TOTALS`` is not a registered return code at all. Validation must
    still reach a verdict on the sealed row, because packages are immutable and
    re-validatable long after the registry has moved on — and because
    ``_movement_findings`` reads a PRIOR package's snapshot with no generator
    anywhere in reach.
    """
    assert get_definition("TEST-TOTALS") is None

    bank = _bank(db_session)
    package = _package(
        db_session,
        bank,
        totals=[],
        provenance={
            "authority": ReportAuthority.TEMPLATE_FORMULA.value,
            "template_hash": "1" * 64,
            "official_workbook": "BSD2 BALANCE SHEET.xlsx",
            "formula_cells_evaluated": 536,
        },
    )
    db_session.commit()

    result = validation.validate_package(db_session, CTX, bank.id, package.id)
    assert result.validation_report is not None
    assert result.validation_report.error_count == 0, result.validation_report.findings


def test_the_bog_generator_and_the_rule_agree_on_the_declaration() -> None:
    """One statement, two readers — the generator writes what the rule reads.

    A drift here (the generator stops declaring, or the rule starts asking for
    something else) is the exact regression that made 23 returns unfileable.
    """
    provenance = build_template_provenance(
        definition=REGISTRY["BSD2"],
        bank=_STUB_BANK,
        effective_date=PERIOD_END,
        form_code="BSD2",
        workbook=form_spec("BSD2").workbook,
        authority_counts={},
        formula_cells_evaluated=536,
    ).to_dict()
    assert provenance["authority"] == validation._TEMPLATE_AUTHORITY  # noqa: SLF001
    assert (
        validation._template_authoritative_rollups(  # noqa: SLF001
            {"totals": [], "provenance": provenance}
        )
        is not None
    )
