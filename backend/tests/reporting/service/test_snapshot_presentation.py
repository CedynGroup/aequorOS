from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.attestation.service.digests import content_digest
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.presentation import (
    LOCKED_TEXT,
    SESSION_STATE,
    SESSION_SUPPORT,
    PresentationState,
)
from app.identity.public import Bank
from app.identity.schemas.institution_profile import (
    RelatedPartyCreate,
    RelatedPartyRoleInput,
    ShareholdingCreate,
)
from app.identity.service import institution_profile
from app.models import BankReportingPeriod, RegulatoryPackage
from app.reporting.service import common, generation, lrt_generation, snapshot_diff
from app.reporting.service.registry import get_definition
from tests.api import test_impersonation_auth as impersonation
from tests.core import test_bank_field_encryption as fields
from tests.support.helpers import ORG_1, USER_1

provider = fields.provider
_OBJECT = TypeAdapter(dict[str, object])
_OBJECTS = TypeAdapter(list[dict[str, object]])


def party_snapshot(db: Session) -> dict[str, object]:
    party = institution_profile.create_related_party(
        db,
        fields.CTX,
        fields.BANK_A,
        RelatedPartyCreate(
            reason="Synthetic filing presentation",
            party_type="individual",
            full_name=fields.NAME,
            contact={"email": "private@example.test"},
            roles=[
                RelatedPartyRoleInput(role="shareholder"),
                RelatedPartyRoleInput(role="ultimate_beneficial_owner"),
                RelatedPartyRoleInput(
                    role="director", term_of_appointment="Private term", annual_fees=Decimal("123")
                ),
                RelatedPartyRoleInput(
                    role="other_key_management", other_responsibilities="Private duties"
                ),
                RelatedPartyRoleInput(role="external_auditor", icag_registration="123456"),
                RelatedPartyRoleInput(
                    role="outsourced_individual", other_responsibilities="Private services"
                ),
                RelatedPartyRoleInput(role="liquidator"),
            ],
        ),
    )
    institution_profile.add_shareholding(
        db,
        fields.CTX,
        fields.BANK_A,
        party.id,
        ShareholdingCreate(
            reason="Synthetic holding",
            share_type="ordinary",
            shareholder_rights="voting",
            number_of_shares=Decimal("100"),
            pct_shareholding=Decimal("25"),
            ubo_party_id=party.id,
        ),
    )
    period = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=fields.BANK_A,
        period_start=fields.AS_OF,
        period_end=fields.AS_OF,
        label="Synthetic",
        status="open",
    )
    db.add(period)
    db.flush()
    bank = db.get(Bank, fields.BANK_A)
    assert bank is not None
    definition = get_definition("LRT-PARTY")
    assert definition is not None
    generated = lrt_generation.generate_lrt_party(db, fields.CTX, bank, period, definition)
    return cast(dict[str, object], generated.snapshot)


def snapshot_pair(db: Session, return_code: str) -> tuple[RegulatoryPackage, RegulatoryPackage]:
    snapshot = party_snapshot(db)
    sections = _OBJECTS.validate_python(snapshot["sections"])
    if return_code == "LRT-CAPITAL":
        sections = [section for section in sections if section["code"] == "shareholders"]
        sections[0]["code"] = "shareholder_register"
    elif return_code == "ICAAP-REPORT":
        for section in sections:
            section["code"] = f"annex_LRT-PARTY__{section['code']}"
    sections.append(
        {
            "code": "capital",
            "title": "Capital",
            "rows": [{"code": "capital", "description": "Capital held", "value": "100"}],
        }
    )
    snapshot["sections"] = sections
    snapshot["return_code"] = return_code
    definition = get_definition(return_code)
    assert definition is not None
    base = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=fields.BANK_A,
        return_family=definition.family,
        return_code=return_code,
        reporting_date=fields.AS_OF,
        frequency=definition.frequency,
        status="superseded",
        version=1,
        generated_by=USER_1,
        snapshot=copy.deepcopy(snapshot),
        content_digest=content_digest(snapshot),
        snapshot_sha256=generation.snapshot_content_hash(snapshot),
    )
    target_snapshot = copy.deepcopy(snapshot)
    target_sections = _OBJECTS.validate_python(target_snapshot["sections"])
    for section in target_sections:
        rows = _OBJECTS.validate_python(section["rows"])
        if str(section["code"]).endswith("auditors"):
            rows[0]["value"] = "987654"
        if section["code"] == "shareholder_register":
            rows[0]["value"] = "30"
        if section["code"] == "capital":
            rows[0]["value"] = "140"
        section["rows"] = rows
    target_snapshot["sections"] = target_sections
    target = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=fields.BANK_A,
        return_family=definition.family,
        return_code=return_code,
        reporting_date=fields.AS_OF,
        frequency=definition.frequency,
        status="generated",
        version=2,
        generated_by=USER_1,
        snapshot=target_snapshot,
        content_digest=content_digest(target_snapshot),
        snapshot_sha256=generation.snapshot_content_hash(target_snapshot),
    )
    db.add_all([base, target])
    db.commit()
    return base, target


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("return_code", ["LRT-PARTY", "LRT-CAPITAL", "ICAAP-REPORT"])
def test_inspector_snapshot_and_comparison_mask_identity_without_altering_evidence(
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    connected: bool,
    return_code: str,
) -> None:
    if connected:
        fields.connect(db_session, provider)
    base, target = snapshot_pair(db_session, return_code)
    snapshot = copy.deepcopy(base.snapshot)
    evidence = {
        row.id: (json.dumps(row.snapshot, sort_keys=True), row.content_digest, row.snapshot_sha256)
        for row in (base, target)
    }
    ordinary = common.read_package(db_session, base)
    assert ordinary.snapshot == snapshot
    ordinary_diff = snapshot_diff.build_comparison(db_session, base, target)
    assert ordinary_diff.changed_count == 2
    token = impersonation._impersonation_token(ORG_1)  # pyright: ignore[reportPrivateUsage]
    headers = {"Authorization": f"Bearer {token}"}

    url = f"/api/v1/banks/{fields.BANK_A}/regulatory-packages/{base.id}"
    detail = db_client.get(url, headers=headers)
    comparison = db_client.get(
        f"{url}/comparison", params={"against": str(target.id)}, headers=headers
    )
    for response in (detail, comparison):
        assert response.status_code == 200, response.text
        for secret in (
            fields.NAME,
            "private@example.test",
            "Private term",
            "Private duties",
            "Private services",
            "123456",
            "987654",
        ):
            assert secret not in response.text
        assert LOCKED_TEXT in response.text
        assert json.loads(response.headers["X-Aequoros-Protected-Data"]) == {
            fields.BANK_A: "support"
        }
    shown = _OBJECT.validate_json(detail.content)
    shown_snapshot = _OBJECT.validate_python(shown["snapshot"])
    shown_sections = _OBJECTS.validate_python(shown_snapshot["sections"])
    for section in shown_sections:
        for row in _OBJECTS.validate_python(section["rows"]):
            if section["code"] != "capital":
                assert row["description"] == LOCKED_TEXT
            if (
                str(section["code"]).endswith("shareholders")
                or section["code"] == "shareholder_register"
            ):
                assert Decimal(str(row["value"])) == 25
                assert Decimal(str(row["number_of_shares"])) == 100
            if str(section["code"]).endswith("directors"):
                assert Decimal(str(row["annual_fees"])) == 123
    shown_diff = _OBJECT.validate_json(comparison.content)
    assert shown_diff["changed_count"] == ordinary_diff.changed_count
    assert shown_diff["identical"] is False
    capital = next(
        section
        for section in _OBJECTS.validate_python(shown_diff["sections"])
        if section["code"] == "capital"
    )
    assert _OBJECTS.validate_python(capital["lines"])[0]["delta"] == "40"
    db_session.expire_all()
    for package_id, original in evidence.items():
        stored = db_session.get(RegulatoryPackage, package_id)
        assert stored is not None
        assert (
            json.dumps(stored.snapshot, sort_keys=True),
            stored.content_digest,
            stored.snapshot_sha256,
        ) == original
    assert base.snapshot == snapshot


@pytest.mark.parametrize("support_mode", ["flag", "state"])
def test_support_snapshot_reads_preserve_loaded_orm_state_and_normal_reads(
    db_session: Session,
    provider: LocalKeyProvider,
    support_mode: str,
) -> None:
    fields.connect(db_session, provider)
    base, target = snapshot_pair(db_session, "LRT-PARTY")
    originals = [copy.deepcopy(base.snapshot), copy.deepcopy(target.snapshot)]
    if support_mode == "flag":
        db_session.info[SESSION_SUPPORT] = True
    else:
        db_session.info[SESSION_STATE] = PresentationState(support=True)
    presented = common.read_package(db_session, base)
    compared = snapshot_diff.build_comparison(db_session, base, target)
    assert fields.NAME not in presented.model_dump_json()
    assert "123456" not in compared.model_dump_json()
    assert LOCKED_TEXT in presented.model_dump_json()
    assert [base.snapshot, target.snapshot] == originals
    db_session.info.pop(SESSION_SUPPORT, None)
    db_session.info.pop(SESSION_STATE, None)
    assert common.read_package(db_session, base).snapshot == originals[0]
    assert "123456" in snapshot_diff.build_comparison(db_session, base, target).model_dump_json()
