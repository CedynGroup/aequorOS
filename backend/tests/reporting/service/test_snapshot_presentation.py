from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta
from functools import cache

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management.presentation import LOCKED_JSON, SESSION_SUPPORT
from app.icaap.domain.frameworks import registry as frameworks
from app.icaap.domain.frameworks.schema import FilingAnnex, Framework
from app.icaap.schemas.workspace import IcaapFreezeCreate
from app.icaap.service import cycles, freeze, workflow
from app.models import RegulatoryPackage, RelatedParty, RelatedPartyRole
from app.reporting.schemas.packages import RegulatoryPackageCreate
from app.reporting.service import common, generation, snapshot_diff, validation
from tests.api import test_impersonation_auth as impersonation
from tests.core import test_key_unavailable_numeric as encrypted
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.icaap.service import p3_support as p3
from tests.reporting.service.bog_forms import test_bsd8 as bsd8
from tests.reporting.service.bog_forms import test_bsd13 as bsd13
from tests.services import test_le_and_lmt as le
from tests.services import test_lrt_packs as lrt
from tests.support.helpers import ORG_1, headers

extra_frameworks = p3.extra_frameworks
pytestmark = pytest.mark.usefixtures(
    "return_generation_authority", "capital_run_authority", "extra_frameworks"
)
_OBJECT = TypeAdapter(dict[str, object])
_OBJECTS = TypeAdapter(list[dict[str, object]])


def freeze_annex(db: Session, monkeypatch: pytest.MonkeyPatch) -> RegulatoryPackage:
    published = frameworks.all_frameworks()
    framework = frameworks.get(*p3.TEST_FRAMEWORK)
    assert framework.filing is not None
    amended = replace(
        framework,
        filing=replace(
            framework.filing,
            annexes=(FilingAnnex("LRT-PARTY", False, ()),),
        ),
    )

    @cache
    def published_frameworks() -> tuple[Framework, ...]:
        return tuple(
            amended if (item.code, item.version) == (framework.code, framework.version) else item
            for item in published
        )

    with monkeypatch.context() as config:
        config.setattr(frameworks, "_default", published_frameworks)
        access = p3.access_for(db)
        p3.govern_first_as_of(db, p3.AS_OF)
        p3.seal_capital_run(db, access)
        generation.generate_package(
            db,
            access.ctx,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(
                return_code="LRT-PARTY",
                reporting_date=p3.AS_OF,
            ),
        )
        cycle = cycles.create_cycle(db, access, p3.annual_payload())
        p3.write_every_section(db, access, cycle.id)
        p3.answer_every_requirement(db, access, cycle.id)
        p3.make_p2_ready(db, access, cycle.id)
        p3.attach(db, access, cycle.id, "senior_management_report", sha="a" * 64)
        p3.submit(db, access, cycle.id)
        reviewer = p3.access_for(
            db,
            p3.make_user(
                db, email="cro@example.test", name="Reviewer", job_title="Chief Risk Officer"
            ),
        )
        p3.decide(db, reviewer, cycle.id, 2, "reviewed")
        approver = p3.access_for(db, p3.make_user(db, email="brc@example.test", name="Approver"))
        p3.decide(db, approver, cycle.id, 3, "approved")
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        result = freeze.freeze_cycle(
            db,
            access,
            cycle.id,
            IcaapFreezeCreate(
                review_digest=digest,
                reason="Freeze actual annex presentation fixture",
            ),
        )
        package = db.get(RegulatoryPackage, result.package.id)
        assert package is not None
        assert any(
            str(section["code"]).startswith("annex_LRT-PARTY__")
            for section in _OBJECTS.validate_python(package.snapshot["sections"])
        )
        return package


def generated_package(db: Session, code: str) -> RegulatoryPackage:
    if code == "LE-MONTHLY":
        le._run_capital_baseline(db)  # pyright: ignore[reportPrivateUsage]
        le._seed_le_book(db, le._tier1(db))  # pyright: ignore[reportPrivateUsage]
    elif code == "LMT":
        le._run_liquidity_baseline(db)  # pyright: ignore[reportPrivateUsage]
        le._seed_lmt_book(db)  # pyright: ignore[reportPrivateUsage]
    elif code == "BSD8":
        previous = le.REPORTING_DATE.replace(day=1) - timedelta(days=1)
        bsd8._seed_classified_book(db, le.REPORTING_DATE, previous)  # pyright: ignore[reportPrivateUsage]
    elif code == "BSD13":
        bsd13._seed_book(db, le.REPORTING_DATE)  # pyright: ignore[reportPrivateUsage]
    db.commit()
    result = generation.generate_package(
        db,
        lrt.MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(
            return_code=code,
            reporting_date=le.REPORTING_DATE,
            notes="Synthetic private filing note",
        ),
    )
    package = db.get(RegulatoryPackage, result.id)
    assert package is not None
    validation.validate_package(db, lrt.MAKER, SAMPLE_BANK_ID, package.id)
    return package


def changed_party_version(db: Session) -> RegulatoryPackage:
    auditor = db.scalar(
        select(RelatedPartyRole)
        .join(
            RelatedParty,
            RelatedParty.id == RelatedPartyRole.party_id,
        )
        .where(
            RelatedPartyRole.organization_id == ORG_1,
            RelatedParty.bank_id == SAMPLE_BANK_ID,
            RelatedPartyRole.role == "external_auditor",
        )
    )
    assert auditor is not None
    auditor.icag_registration = "Synthetic private comparison registration"
    db.flush()
    read = generation.generate_package(
        db,
        lrt.MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(
            return_code="LRT-PARTY",
            reporting_date=le.REPORTING_DATE,
        ),
    )
    target = db.get(RegulatoryPackage, read.id)
    assert target is not None
    return target


def assert_staff_bodies_hidden(
    package: RegulatoryPackage,
    identity: str | None,
    detail: Response,
    compared: Response,
) -> None:
    for response in (detail, compared):
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        if identity is not None:
            assert identity not in response.text
    body = _OBJECT.validate_json(detail.content)
    assert (body["return_code"], body["version"], body["reporting_date"]) == (
        package.return_code,
        package.version,
        package.reporting_date.isoformat(),
    )
    assert body["status"] == package.status and body["generated_at"]
    assert body["snapshot"] == {LOCKED_JSON: "support"}
    assert body["validation_report"] is None and body["validation_passed"] is None
    assert (body["source_runs"], body["approvals"], body["declared_methodologies"]) == ([], [], [])
    assert body["notes"] is None and body["regulator_comments"] is None
    diff = _OBJECT.validate_json(compared.content)
    assert diff["content_locked"] is True and diff["sections"] == []
    for field in (
        "identical",
        "changed_count",
        "added_count",
        "removed_count",
        "unchanged_section_count",
    ):
        assert diff[field] is None
    assert _OBJECT.validate_python(diff["base"])["package_id"] == str(package.id)


@pytest.mark.parametrize(
    "code",
    [
        "LRT-PROFILE",
        "LRT-OUTLET",
        "LRT-PARTY",
        "LRT-CAPITAL",
        "LRT-PRODUCT",
        "LE-MONTHLY",
        "LMT",
        "BSD8",
        "BSD11",
        "BSD13",
        "ICAAP-REPORT",
    ],
)
@pytest.mark.usefixtures("institution_profile_authority")
def test_enabled_key_support_reads_are_metadata_only_for_real_generated_packages(
    db_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    materialize_canonical_test_book(db_session)
    encrypted.connect(db_session, monkeypatch)
    encrypted.register(db_session)
    package = (
        freeze_annex(db_session, monkeypatch)
        if code == "ICAAP-REPORT"
        else generated_package(db_session, code)
    )
    target = changed_party_version(db_session) if code == "LRT-PARTY" else package
    db_session.commit()
    db_session.refresh(package)
    original = copy.deepcopy(package.snapshot)
    seals = (package.content_digest, package.snapshot_sha256, package.register_state_digest)
    url = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package.id}"
    ordinary = db_client.get(url, headers=headers())
    assert ordinary.status_code == 200, ordinary.text
    assert _OBJECT.validate_json(ordinary.content)["snapshot"] == original
    identity = {
        "LRT-PARTY": "Ama Mensah",
        "LRT-CAPITAL": "Golden Coast Holdings Ltd",
        "LE-MONTHLY": "Akwaaba Industries Ltd",
        "LMT": "Kanda Pensions Trust",
        "BSD8": "Kumasi Traders Ltd",
        "BSD11": "Ama Mensah",
        "BSD13": "Citibank NA",
        "ICAAP-REPORT": "Ama Mensah",
    }.get(code)
    if identity is not None:
        assert identity in ordinary.text
    normal_comparison = db_client.get(
        f"{url}/comparison", params={"against": str(target.id)}, headers=headers()
    )
    assert normal_comparison.status_code == 200, normal_comparison.text
    assert _OBJECT.validate_json(normal_comparison.content)["identical"] == (
        package.id == target.id
    )
    if code == "LRT-PARTY":
        assert "Synthetic private comparison registration" in normal_comparison.text
    token = impersonation._impersonation_token(ORG_1)  # pyright: ignore[reportPrivateUsage]
    staff_headers = {"Authorization": f"Bearer {token}"}
    detail = db_client.get(url, headers=staff_headers)
    compared = db_client.get(
        f"{url}/comparison", params={"against": str(target.id)}, headers=staff_headers
    )
    assert_staff_bodies_hidden(package, identity, detail, compared)
    listing = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        params={"return_code": code},
        headers=staff_headers,
    )
    assert listing.status_code == 200, listing.text
    rows = _OBJECTS.validate_python(_OBJECT.validate_json(listing.content)["packages"])
    assert rows and all(row["notes"] is None and row["regulator_comments"] is None for row in rows)
    db_session.info[SESSION_SUPPORT] = True
    assert common.read_package(db_session, package).snapshot == {LOCKED_JSON: "support"}
    assert snapshot_diff.build_comparison(db_session, package, target).content_locked is True
    assert package.snapshot == original
    db_session.info.pop(SESSION_SUPPORT)
    db_session.expire_all()
    assert package.snapshot == original
    assert (package.content_digest, package.snapshot_sha256, package.register_state_digest) == seals
    restored = db_client.get(url, headers=headers())
    assert _OBJECT.validate_json(restored.content)["snapshot"] == original
