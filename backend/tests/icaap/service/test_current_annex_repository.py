"""Real SQL current-annex contracts; resolved fake answers cannot prove these."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.icaap.domain.annexes import AnnexKey, AnnexVersion
from app.icaap.service.current_annexes import PostgresCurrentAnnexRepository
from app.icaap.service.validation_rules import findings
from app.models import Bank, RegulatoryPackage
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

pytestmark = pytest.mark.database_contract
AS_OF = date(2025, 12, 31)
BANK_ID = "BK-ANNEX001"
ANNEX_CODE = "ICAAP-STRESS-APPENDIX2"
SIBLING_BANK = "BK-ANNEX002"
OTHER_BANK = "BK-ANNEX003"
KEY = AnnexKey(ORG_1, BANK_ID, ANNEX_CODE, AS_OF, "solo")


@pytest.fixture
def annex_db(db_session: Session) -> Session:
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("Current-annex SQL and bank isolation require disposable PostgreSQL")
    for bank_id, org in ((BANK_ID, ORG_1), (SIBLING_BANK, ORG_1), (OTHER_BANK, ORG_2)):
        db_session.add(
            Bank(
                id=bank_id,
                organization_id=org,
                name="Synthetic annex contract bank",
                short_name=bank_id,
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
    db_session.flush()
    return db_session


def _package(  # noqa: PLR0913 - explicit stored selection dimensions
    db: Session,
    key: AnnexKey = KEY,
    *,
    version: int = 2,
    status: str = "generated",
    digest: str | None = "a" * 64,
    rehearsal: bool = False,
) -> RegulatoryPackage:
    row = RegulatoryPackage(
        organization_id=key.organization_id,
        bank_id=key.bank_id,
        return_code=key.return_code,
        return_family="icaap",
        reporting_date=key.reporting_date,
        frequency="annual",
        basis=key.basis,
        status=status,
        version=version,
        content_digest=digest,
        is_rehearsal=rehearsal,
        generated_by=USER_1 if key.organization_id == ORG_1 else USER_2,
        snapshot={},
    )
    db.add(row)
    db.flush()
    return row


def _version(package: RegulatoryPackage) -> AnnexVersion:
    return AnnexVersion(package.id, package.version, package.content_digest)


def test_the_current_annex_is_read_from_the_managed_session(annex_db: Session) -> None:
    package = _package(annex_db)
    assert PostgresCurrentAnnexRepository(annex_db).current(KEY) == _version(package)


def test_a_missing_current_annex_returns_no_version(annex_db: Session) -> None:
    assert PostgresCurrentAnnexRepository(annex_db).current(KEY) is None


def test_superseded_versions_are_ineligible_even_with_a_higher_number(annex_db: Session) -> None:
    current = _package(annex_db)
    _ = _package(annex_db, version=99, status="superseded", digest="b" * 64)
    repository = PostgresCurrentAnnexRepository(annex_db)
    assert repository.current(KEY) == _version(current)
    current.status = "superseded"
    annex_db.flush()
    assert repository.current(KEY) is None


def test_the_highest_eligible_version_is_selected(annex_db: Session) -> None:
    # The existing query includes both rehearsal and non-rehearsal rows.
    # Their independent current-version indexes permit two eligible versions.
    _ = _package(annex_db, version=1)
    latest = _package(annex_db, version=4, rehearsal=True, digest="b" * 64)
    assert PostgresCurrentAnnexRepository(annex_db).current(KEY) == _version(latest)


@pytest.mark.parametrize("scope", ["basis", "date", "code", "bank", "organization"])
def test_every_scope_dimension_is_enforced_by_real_sql(annex_db: Session, scope: str) -> None:
    current = _package(annex_db)
    other_key = {
        "basis": replace(KEY, basis="consolidated"),
        "date": replace(KEY, reporting_date=AS_OF + timedelta(days=1)),
        "code": replace(KEY, return_code="ICAAP-UPDATE"),
        "bank": replace(KEY, bank_id=SIBLING_BANK),
        "organization": replace(KEY, organization_id=ORG_2, bank_id=OTHER_BANK),
    }[scope]
    other = _package(annex_db, other_key, version=99, digest="b" * 64)
    repository = PostgresCurrentAnnexRepository(annex_db)
    assert repository.current(KEY) == _version(current)
    assert repository.current(other_key) == _version(other)
    if scope == "organization":
        # Bank identity alone must not replace the organization predicate.
        assert repository.current(replace(KEY, organization_id=ORG_2)) is None


def test_nullable_stored_digests_are_preserved(annex_db: Session) -> None:
    package = _package(annex_db, digest=None)
    assert PostgresCurrentAnnexRepository(annex_db).current(KEY) == _version(package)


def test_production_validation_reads_current_annexes_from_postgres(annex_db: Session) -> None:
    current = _package(annex_db)
    report = RegulatoryPackage(
        organization_id=KEY.organization_id,
        bank_id=KEY.bank_id,
        return_code="ICAAP-REPORT",
        reporting_date=KEY.reporting_date,
        basis=KEY.basis,
        snapshot={
            "metadata": {
                "icaap": {
                    "annexes": [
                        {"return_code": ANNEX_CODE, "content_digest": current.content_digest}
                    ]
                }
            }
        },
    )
    before = findings(annex_db, report)
    assert [entry for entry in before if entry["rule"] == "icaap.annex_current"] == []
    current.status = "superseded"
    annex_db.flush()
    _ = _package(annex_db, version=3, digest="b" * 64)
    after = [
        entry for entry in findings(annex_db, report) if entry["rule"] == "icaap.annex_current"
    ]
    assert len(after) == 1
    assert after[0]["severity"] == "ERROR"
    assert "different content" in after[0]["detail"]
