"""The two draft-export routes: who may call them, and what comes back.

The export routes are the first ICAAP surface that hands a whole document to a
caller, so the interesting questions are all about the gate in front of them:

* with the workspace flag off, the path must not exist at all — an unreleased
  surface should not be discoverable from a production deployment;
* an SDI is outside the ICAAP regime and gets 404, not 403, even holding a
  perfectly good capital binding;
* another tenant's bank is 404;
* a caller without a complete CAP/confidential binding gets 403 on their own
  bank, because their own institution's existence is not a secret from them;
* export authority specifically — a read-only bundle may look, an analyst may
  export.

Then the documents themselves: the right content types, a filename that says
DRAFT, and an audit row per export.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import zipfile
from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pdfplumber
import pytest
from docx import Document as read_document
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.domain.icaap.frameworks import registry
from app.features import export_icaap_drafts
from app.models import AuditEvent, AuthorizationBinding, Bank, User
from app.models.icaap import IcaapBlockBinding, IcaapCycle, IcaapDataBlock, IcaapSection
from app.services import authorization
from app.services.icaap import parameters, readiness
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers

BANK_ID = "BK-ICAAP001"
SDI_BANK_ID = "BK-ICAAPSD1"
OTHER_TENANT_BANK_ID = "BK-ICAAPOT1"
FRAMEWORK_CODE = "bog_icaap"
FISCAL_YEAR = 2025
AS_OF = date(2025, 12, 31)


def _base(bank_id: str = BANK_ID) -> str:
    return f"/api/v1/banks/{bank_id}/icaap"


@pytest.fixture(autouse=True)
def _workspace_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _session(organization_id: str = ORG_1):  # noqa: ANN202 - a plain Session
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    return session


def _framework() -> Any:
    frameworks = registry.all_frameworks()
    for framework in frameworks:
        if framework.jurisdiction == "GH":
            return framework
    return frameworks[0]


@pytest.fixture(autouse=True)
def _tenant(db_client: TestClient) -> None:
    """A bank with no fixture-wide authority, plus a sibling SDI and a bank in
    another tenant. Every test then grants exactly what it means to test."""
    session = _session()
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        if session.get(Bank, BANK_ID) is None:
            session.add(
                Bank(
                    id=BANK_ID,
                    organization_id=ORG_1,
                    name="ICAAP Draft Bank Ltd",
                    short_name="Draft Bank",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        if session.get(Bank, SDI_BANK_ID) is None:
            session.add(
                Bank(
                    id=SDI_BANK_ID,
                    organization_id=ORG_1,
                    name="ICAAP Savings & Loans",
                    short_name="Draft SDI",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="savings_and_loans",
                    institution_type="savings_and_loans",
                )
            )
        session.commit()
    finally:
        session.close()

    other = _session(ORG_2)
    try:
        if other.get(Bank, OTHER_TENANT_BANK_ID) is None:
            other.add(
                Bank(
                    id=OTHER_TENANT_BANK_ID,
                    organization_id=ORG_2,
                    name="Other Tenant Bank",
                    short_name="Other",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
            other.commit()
    finally:
        other.close()


def _grant(
    *,
    bundle: RoleBundle = RoleBundle.ANALYST,
    module: ModuleScope = ModuleScope.CAPITAL,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    institution_id: str | None = BANK_ID,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
) -> int:
    session = _session()
    try:
        user = session.get(User, USER_1)
        assert user is not None
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope, institution_id, module, sensitivity
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise ICAAP draft-export authorization.",
        )
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _make_cycle(
    *,
    bank_id: str = BANK_ID,
    organization_id: str = ORG_1,
    cycle_kind: str = "annual",
    status: str = "draft",
) -> UUID:
    """A cycle and its section rows, written directly.

    The export is what is under test, not cycle creation, so this seeds the
    rows a created cycle would have rather than driving the create route.
    """
    framework = _framework()
    session = _session(organization_id)
    try:
        cycle = IcaapCycle(
            organization_id=organization_id,
            bank_id=bank_id,
            fiscal_year=FISCAL_YEAR,
            as_of_date=AS_OF,
            cycle_kind=cycle_kind,
            basis="solo",
            subsidiaries_declared=False,
            title=f"ICAAP FY{FISCAL_YEAR}",
            framework_code=framework.code,
            framework_version=framework.version,
            framework_sha256=framework.digest,
            status=status,
            round=1,
            due_date=date(FISCAL_YEAR + 1, 3, 31),
            due_date_basis="framework",
            created_by=USER_1,
        )
        session.add(cycle)
        session.flush()
        for definition in framework.sections:
            session.add(
                IcaapSection(
                    organization_id=organization_id,
                    bank_id=bank_id,
                    cycle_id=cycle.id,
                    section_key=definition.key,
                    letter=definition.letter,
                    position=definition.order,
                    working_doc={"type": "doc", "content": []},
                    working_rev=0,
                    checklist_state={},
                )
            )
        session.commit()
        return cycle.id
    finally:
        session.close()


def _write_section(cycle_id: UUID, section_key: str, text: str) -> None:
    session = _session()
    try:
        row = session.scalar(
            select(IcaapSection).where(
                IcaapSection.cycle_id == cycle_id, IcaapSection.section_key == section_key
            )
        )
        assert row is not None
        row.working_doc = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        }
        row.working_rev = 1
        session.commit()
    finally:
        session.close()


def _bind_capital_block(cycle_id: UUID) -> UUID:
    """A bound `capital_position` block, written as a resolver would leave it."""
    payload = {
        "schema": "icaap-block-payload-v1",
        "title": "Capital position",
        "as_of": AS_OF.isoformat(),
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
                "rows": [{"cells": {"label": "CET1 capital", "amount": "1234567"}}],
            }
        ],
    }
    facts = {
        "car_pct": {"label": "Total capital ratio", "kind": "ratio_pct", "value": "17.08"},
        "total_capital": {"label": "Total capital", "kind": "amount", "value": None},
    }
    session = _session()
    try:
        block = IcaapDataBlock(
            organization_id=ORG_1,
            bank_id=BANK_ID,
            cycle_id=cycle_id,
            block_type="capital_position",
            block_key="capital_position",
            title=None,
            params={},
            created_by=USER_1,
        )
        session.add(block)
        session.flush()
        session.add(
            IcaapBlockBinding(
                organization_id=ORG_1,
                bank_id=BANK_ID,
                cycle_id=cycle_id,
                block_id=block.id,
                seq=1,
                resolver="capital_position",
                resolver_version="1",
                source_kind="run",
                source_ref={"run_id": "run-abc", "input_hash": "bank-facts-v3:7c1e"},
                source_key="run:run-abc",
                source_as_of=AS_OF,
                source_run_ids=["run-abc"],
                payload=payload,
                facts=facts,
                payload_sha256="a" * 64,
                bound_by=USER_1,
            )
        )
        session.commit()
        return block.id
    finally:
        session.close()


def _write_block_reference(cycle_id: UUID, section_key: str, block_id: UUID) -> None:
    """A section whose prose quotes one fact and embeds the block's tables."""
    session = _session()
    try:
        row = session.scalar(
            select(IcaapSection).where(
                IcaapSection.cycle_id == cycle_id, IcaapSection.section_key == section_key
            )
        )
        assert row is not None
        row.working_doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "The total capital ratio is "},
                        {
                            "type": "factRef",
                            "attrs": {"blockId": str(block_id), "factKey": "car_pct"},
                        },
                        {"type": "text", "text": ", and total capital is "},
                        {
                            "type": "factRef",
                            "attrs": {"blockId": str(block_id), "factKey": "total_capital"},
                        },
                        {"type": "text", "text": "."},
                    ],
                },
                {"type": "dataBlock", "attrs": {"blockId": str(block_id)}},
            ],
        }
        row.working_rev = 1
        session.commit()
    finally:
        session.close()


def _export_events() -> list[AuditEvent]:
    session = _session()
    try:
        return list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "icaap.draft.exported")
            ).all()
        )
    finally:
        session.close()


class TestAuthorization:
    def test_an_sdi_is_outside_the_regime(self, db_client: TestClient) -> None:
        # 404, not 403, and holding CAP/confidential authority over the SDI does
        # not change it: the ICAAP workspace is banks-only and its existence is
        # not advertised to an institution that can never file one.
        version = _grant(institution_id=SDI_BANK_ID)
        response = db_client.get(
            f"{_base(SDI_BANK_ID)}/cycles/{uuid4()}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 404

    def test_another_tenants_bank_is_not_found(self, db_client: TestClient) -> None:
        version = _grant(institution_scope=InstitutionScope.ORGANIZATION, institution_id=None)
        response = db_client.get(
            f"{_base(OTHER_TENANT_BANK_ID)}/cycles/{uuid4()}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 404

    def test_another_tenants_cycle_is_not_found(self, db_client: TestClient) -> None:
        # The bank resolves, the binding is good — but the cycle belongs to
        # another organization, so it must not be reachable through this path.
        version = _grant()
        foreign_cycle = _make_cycle(bank_id=OTHER_TENANT_BANK_ID, organization_id=ORG_2)
        response = db_client.get(
            f"{_base()}/cycles/{foreign_cycle}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 404

    def test_an_unknown_cycle_is_not_found(self, db_client: TestClient) -> None:
        version = _grant()
        response = db_client.get(
            f"{_base()}/cycles/{uuid4()}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 404

    def test_no_binding_is_refused_on_the_callers_own_bank(self, db_client: TestClient) -> None:
        cycle_id = _make_cycle()
        response = db_client.get(f"{_base()}/cycles/{cycle_id}/draft.pdf", headers=headers())
        assert response.status_code == 403

    def test_an_aggregated_binding_is_not_enough(self, db_client: TestClient) -> None:
        # An ICAAP is confidential throughout: aggregated sensitivity does not
        # reach it, however broad the module scope.
        version = _grant(sensitivity=SensitivityScope.AGGREGATED)
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 403

    def test_a_binding_for_another_module_is_not_enough(self, db_client: TestClient) -> None:
        version = _grant(module=ModuleScope.FX)
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 403

    def test_a_viewer_bundle_has_no_export_authority(self, db_client: TestClient) -> None:
        version = _grant(bundle=RoleBundle.VIEWER)
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 403

    def test_a_scalar_admin_role_alone_is_not_authority(self, db_client: TestClient) -> None:
        # Token role claims are ignored by the evaluator; only a binding counts.
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(roles=("admin", "analyst")),
        )
        assert response.status_code == 403


class TestPdfExport:
    def test_an_analyst_gets_a_watermarked_pdf(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF-")

    def test_the_filename_says_draft(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        disposition = response.headers["content-disposition"]
        assert 'filename="ICAAP-Draft-Bank-FY2025-solo-DRAFT.pdf"' in disposition

    def test_the_authors_text_reaches_the_document(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        section_key = _framework().sections[0].key
        _write_section(cycle_id, section_key, "The Board challenged the capital plan.")
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf?content=working",
            headers=headers(authorization_version=version),
        )
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            body = "\n".join("".join(char["text"] for char in page.chars) for page in pdf.pages)
        assert "The Board challenged the capital plan." in body

    def test_the_committed_view_does_not_fall_back_to_working_text(
        self, db_client: TestClient
    ) -> None:
        # Asking for the committed view is asking what has been signed off
        # internally; answering with uncommitted text would defeat the question.
        version = _grant()
        cycle_id = _make_cycle()
        section_key = _framework().sections[0].key
        _write_section(cycle_id, section_key, "Uncommitted working sentence.")
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf?content=committed",
            headers=headers(authorization_version=version),
        )
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            body = "\n".join("".join(char["text"] for char in page.chars) for page in pdf.pages)
        assert "Uncommitted working sentence." not in body
        assert "No committed text yet." in body
        # ...and it does not label the page "Working draft", which would claim
        # the uncommitted text is what was printed.
        assert "No committed version" in body

    def test_a_rehearsal_is_watermarked_as_one(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle(cycle_kind="rehearsal")
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            body = "".join(char["text"] for char in pdf.pages[0].chars)
        assert "REHEARSAL — not a regulatory filing" in body

    def test_an_invalid_content_mode_is_refused(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf?content=filed",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 422


class TestDocxExport:
    def test_an_analyst_gets_an_editable_working_copy(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.docx",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "word/document.xml" in archive.namelist()

    def test_the_filename_says_draft(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.docx",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200, response.text
        assert (
            'filename="ICAAP-Draft-Bank-FY2025-solo-DRAFT.docx"'
            in response.headers["content-disposition"]
        )

    def test_the_header_says_it_is_not_the_filed_document(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.docx",
            headers=headers(authorization_version=version),
        )
        document = read_document(io.BytesIO(response.content))
        assert "WORKING COPY — not the filed document" in (
            document.sections[0].header.paragraphs[0].text
        )


class TestAudit:
    def test_every_export_is_recorded_with_its_checksum(self, db_client: TestClient) -> None:
        version = _grant()
        cycle_id = _make_cycle()
        before = len(_export_events())
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200
        events = _export_events()
        assert len(events) == before + 1
        details = events[-1].details or {}
        assert details["kind"] == "pdf"
        assert details["content"] == "working"
        assert details["sha256"] == hashlib.sha256(response.content).hexdigest()

    def test_a_refused_export_records_no_document(self, db_client: TestClient) -> None:
        cycle_id = _make_cycle()
        before = len(_export_events())
        response = db_client.get(f"{_base()}/cycles/{cycle_id}/draft.pdf", headers=headers())
        assert response.status_code == 403
        assert len(_export_events()) == before


class TestBoundFigures:
    def _pdf_text(self, db_client: TestClient) -> str:
        version = _grant()
        cycle_id = _make_cycle()
        block_id = _bind_capital_block(cycle_id)
        _write_block_reference(cycle_id, _framework().sections[0].key, block_id)
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200, response.text
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            return "\n".join("".join(char["text"] for char in page.chars) for page in pdf.pages)

    def test_a_quoted_fact_prints_the_bound_figure(self, db_client: TestClient) -> None:
        assert "The total capital ratio is 17.08%" in self._pdf_text(db_client)

    def test_a_fact_the_binding_left_null_is_named_not_zeroed(self, db_client: TestClient) -> None:
        # The resolver captured no value. A "0.00" here would read as a bank
        # holding no capital.
        body = self._pdf_text(db_client)
        assert "total capital is Not available" in body
        assert "total capital is 0" not in body

    def test_the_blocks_table_and_its_provenance_reach_the_document(
        self, db_client: TestClient
    ) -> None:
        body = self._pdf_text(db_client)
        assert "CET1 capital" in body
        assert "1,234,567.00" in body
        assert "run:run-abc" in body


class TestReadinessOnTheCover:
    def test_a_readiness_refusal_does_not_stop_the_draft(
        self, db_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Readiness fails closed on an unconfigured governed parameter (D-024).
        # That must not make the author unable to read their own document: the
        # draft prints and names the gap instead.
        def _refuse(*_args: object, **_kwargs: object) -> object:
            raise parameters.missing_parameter(
                "icaap_deadline_amber_days",
                "ICAAP readiness warns this many days before the filing deadline.",
            )

        monkeypatch.setattr(readiness, "get_readiness", _refuse)
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 200, response.text
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            body = "".join(char["text"] for char in pdf.pages[0].chars)
        assert "Readiness could not be assessed" in body
        assert "icaap_deadline_amber_days" in body

    def test_a_readiness_not_found_still_propagates(
        self, db_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only the 409 family is printed. Anything else is a real failure and
        # must not be swallowed into a line of cover text.
        def _vanish(*_args: object, **_kwargs: object) -> object:
            raise HTTPException(status_code=404, detail="Not Found")

        monkeypatch.setattr(readiness, "get_readiness", _vanish)
        version = _grant()
        cycle_id = _make_cycle()
        response = db_client.get(
            f"{_base()}/cycles/{cycle_id}/draft.pdf",
            headers=headers(authorization_version=version),
        )
        assert response.status_code == 404


class TestNoStorageDependency:
    def test_the_routes_declare_no_storage_dependency(self) -> None:
        # Drafts stream and are never stored, so a deployment whose object
        # storage is unconfigured can still read its own ICAAP.
        source = inspect.getsource(export_icaap_drafts)
        assert "Storage" not in source
