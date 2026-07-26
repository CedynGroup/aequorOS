"""What a prior version can still answer: its files, its signers, its figures.

The assertions are about defects a bank would meet in production, not about
shape. Every one of them is drawn from a live BSD3 chain in which some versions
were never exported at all and one holds two signatures from an attestation
that was subsequently withdrawn.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AttestationSignature,
    BankReportingPeriod,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import regulatory_liquidity
from app.services.attestation import workflow as attestation_workflow
from app.services.regulatory_reporting import generation, snapshot_diff, version_chain
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.api.helpers import ORG_2

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
NEIGHBOUR = TenantContext(organization_id=ORG_2, actor_user_id=uuid4())
REPORTING_DATE = date(2026, 3, 31)


# --- the diff, as a pure function ------------------------------------------


def _snapshot(rows: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "regulatory-package-v1",
        "sections": [{"code": "hqla", "title": "HQLA", "rows": rows, "total": None}],
        "totals": [],
        "metadata": {"generated_at": "2026-03-31T00:00:00+00:00"},
    }
    base.update(overrides)
    return base


def test_diff_reports_changed_added_and_removed_line_items() -> None:
    base = _snapshot(
        [
            {"code": "1.1", "description": "Cash", "value": "100.00"},
            {"code": "1.2", "description": "Bills", "value": "50.00"},
        ]
    )
    target = _snapshot(
        [
            {"code": "1.1", "description": "Cash", "value": "120.00"},
            {"code": "1.3", "description": "Bonds", "value": "9.00"},
        ]
    )
    sections, unchanged_sections = snapshot_diff.diff_snapshots(base, target)
    assert unchanged_sections == 0
    assert len(sections) == 1
    lines = {line.code: line for line in sections[0].lines}
    assert lines["1.1"].change == "changed"
    assert (lines["1.1"].base_value, lines["1.1"].target_value) == ("100.00", "120.00")
    assert lines["1.1"].delta == "20.00"
    assert lines["1.1"].delta_pct == "20.00"
    assert lines["1.3"].change == "added"
    assert (lines["1.3"].base_value, lines["1.3"].target_value) == (None, "9.00")
    assert lines["1.2"].change == "removed"
    assert (lines["1.2"].base_value, lines["1.2"].target_value) == ("50.00", None)


def test_diff_of_a_snapshot_against_itself_is_empty() -> None:
    """The load-bearing case: no drift means no finding, ever.

    A comparison that manufactures differences from identical figures would
    make every real one unbelievable.
    """
    snapshot = _snapshot([{"code": "1.1", "description": "Cash", "value": "100.00"}])
    sections, unchanged_sections = snapshot_diff.diff_snapshots(snapshot, snapshot)
    assert sections == []
    assert unchanged_sections == 1


def test_volatile_generation_metadata_is_not_a_figures_change() -> None:
    """Regenerating identical figures must read as identical figures."""
    rows = [{"code": "1.1", "description": "Cash", "value": "100.00"}]
    base = _snapshot(rows)
    target = _snapshot(rows, metadata={"generated_at": "2026-07-25T09:15:00+00:00"})
    sections, _ = snapshot_diff.diff_snapshots(base, target)
    assert sections == []


def test_a_section_present_in_only_one_version_reports_all_its_rows() -> None:
    """A heading that appears or disappears takes its line items with it."""
    base = _snapshot([{"code": "1.1", "description": "Cash", "value": "100.00"}])
    target = _snapshot([{"code": "1.1", "description": "Cash", "value": "100.00"}])
    target["sections"].append(
        {
            "code": "buffers",
            "title": "Countercyclical buffers",
            "rows": [
                {"code": "b.1", "description": "Buffer", "value": "4.00"},
                {"code": "b.2", "description": "Add-on", "value": "1.00"},
            ],
            "total": {"code": "b.total", "description": "Total buffers", "value": "5.00"},
        }
    )

    sections, _ = snapshot_diff.diff_snapshots(base, target)
    assert [section.code for section in sections] == ["buffers"]
    added = sections[0]
    assert added.change == "added"
    assert {line.change for line in added.lines} == {"added"}
    assert [line.code for line in added.lines] == ["b.1", "b.2", "b.total"]
    assert added.lines[-1].is_total is True

    # Reversed, the same section is entirely removed — never silently dropped.
    reversed_sections, _ = snapshot_diff.diff_snapshots(target, base)
    assert [section.change for section in reversed_sections] == ["removed"]
    assert {line.change for line in reversed_sections[0].lines} == {"removed"}


def test_headline_totals_are_diffed_and_non_numeric_cells_carry_no_delta() -> None:
    base = _snapshot([], totals=[{"code": "lcr_pct", "description": "LCR", "value": "yes"}])
    target = _snapshot([], totals=[{"code": "lcr_pct", "description": "LCR", "value": "no"}])
    sections, _ = snapshot_diff.diff_snapshots(base, target)
    assert [(section.code, section.origin) for section in sections] == [("totals", "totals")]
    line = sections[0].lines[0]
    assert (line.base_value, line.target_value) == ("yes", "no")
    # A fabricated 0.00 here would read as "no movement" on a changed figure.
    assert line.delta is None
    assert line.delta_pct is None


# --- the chain, against real packages --------------------------------------


def _seed_two_versions(db: Session) -> tuple[RegulatoryPackage, RegulatoryPackage]:
    seed_sample_bank(db)
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    payload = RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE)
    first = generation.generate_package(db, MAKER, SAMPLE_BANK_ID, payload)
    second = generation.generate_package(db, MAKER, SAMPLE_BANK_ID, payload)
    return _row(db, first.id), _row(db, second.id)


def _row(db: Session, package_id: UUID) -> RegulatoryPackage:
    package = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == package_id))
    assert package is not None
    return package


def test_a_version_that_was_never_exported_reports_no_files_rather_than_failing(
    db_session: Session,
) -> None:
    """Versions 2 and 3 of the live chain hold no artifact at all.

    The card must be able to say "never exported" — an endpoint that errored,
    or one that reported files it cannot serve, both end in a dead button.
    """
    superseded, current = _seed_two_versions(db_session)

    chain = version_chain.get_version_chain(db_session, MAKER, SAMPLE_BANK_ID, superseded.id)
    assert [entry.version for entry in chain.versions] == [2, 1]
    assert chain.current_package_id == current.id
    prior = chain.versions[1]
    assert prior.package_id == superseded.id
    assert prior.status == "superseded"
    assert prior.is_current is False
    assert prior.artifacts == []
    assert prior.artifact_versions == []
    assert prior.has_retrievable_files is False


def test_an_exported_version_offers_its_files(db_session: Session) -> None:
    superseded, _current = _seed_two_versions(db_session)
    db_session.add(
        RegulatoryPackageArtifact(
            organization_id=DEMO_ORG_ID,
            package_id=superseded.id,
            kind="pdf",
            object_path=f"bog_returns/{REPORTING_DATE.isoformat()}/{superseded.id}/BSD3.pdf",
            checksum_sha256="a" * 64,
            size_bytes=2048,
        )
    )
    db_session.commit()

    chain = version_chain.get_version_chain(db_session, MAKER, SAMPLE_BANK_ID, superseded.id)
    prior = next(entry for entry in chain.versions if entry.package_id == superseded.id)
    assert [artifact.kind for artifact in prior.artifacts] == ["pdf"]
    assert prior.has_retrievable_files is True


#: Opaque by design; any well-formed SGN- id serves, since nothing here
#: re-derives one.
_SIGNER_IDS = {"preparer": "SGN-PPPPPPPPPPPPPPPP", "approver": "SGN-AAAAAAAAAAAAAAAA"}


def _seed_signature(
    db: Session, package: RegulatoryPackage, *, cycle: int, role: str
) -> AttestationSignature:
    signature = AttestationSignature(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        package_id=package.id,
        package_version=package.version,
        signing_role=role,
        officer_title="Head of Finance",
        signer_id=_SIGNER_IDS[role],
        signer_user_id=DEMO_USER_ID,
        signer_display_name="Ama Mensah",
        binding_class="engine_run",
        certification_digest="d" * 64,
        content_digest="c" * 64,
        statement="I certify…",
        attestation_payload={"schema": "aequoros-signature-v1"},
        payload_digest="p" * 64,
        signature_method="detached_ecdsa_p256_sha256",
        signature_value=b"sig",
        certificate_pem="-----BEGIN CERTIFICATE-----",
        certificate_sha256="f" * 64,
        declared_at=datetime.now(UTC),
        prev_hash=attestation_workflow.GENESIS_HASH,
        entry_hash="e" * 64,
        attestation_cycle=cycle,
    )
    db.add(signature)
    db.commit()
    return signature


def test_signatures_from_a_voided_cycle_are_listed_but_flagged_withdrawn(
    db_session: Session,
) -> None:
    """The live v4 case: attestation_state 'unsigned', two signatures on file.

    A void preserves signatures rather than deleting them, so reporting them
    without qualification would tell an operator a withdrawn attestation still
    certifies the return; dropping them would hide evidence the register
    requires to stay legible.
    """
    superseded, _current = _seed_two_versions(db_session)
    _seed_signature(db_session, superseded, cycle=1, role="preparer")
    # The void that happened between then and now.
    superseded.attestation_cycle = 2
    db_session.commit()
    _seed_signature(db_session, superseded, cycle=2, role="approver")

    chain = version_chain.get_version_chain(db_session, MAKER, SAMPLE_BANK_ID, superseded.id)
    prior = next(entry for entry in chain.versions if entry.package_id == superseded.id)
    by_role = {signature.signing_role: signature for signature in prior.signatures}
    assert by_role["preparer"].withdrawn is True
    assert by_role["preparer"].attestation_cycle == 1
    assert by_role["approver"].withdrawn is False
    assert by_role["approver"].attestation_cycle == 2


def test_the_chain_and_comparison_are_unreachable_from_another_tenant(
    db_session: Session,
) -> None:
    superseded, current = _seed_two_versions(db_session)
    with pytest.raises(HTTPException) as chain_error:
        version_chain.get_version_chain(db_session, NEIGHBOUR, SAMPLE_BANK_ID, superseded.id)
    assert chain_error.value.status_code == 404

    with pytest.raises(HTTPException) as comparison_error:
        version_chain.compare_versions(
            db_session, NEIGHBOUR, SAMPLE_BANK_ID, superseded.id, current.id
        )
    assert comparison_error.value.status_code == 404


def test_comparing_a_regenerated_version_with_the_current_one_finds_the_edit(
    db_session: Session,
) -> None:
    superseded, current = _seed_two_versions(db_session)

    identical = version_chain.compare_versions(
        db_session, MAKER, SAMPLE_BANK_ID, superseded.id, current.id
    )
    assert identical.identical is True
    assert identical.sections == []
    assert identical.base.version == 1
    assert identical.target.version == 2

    # Move one figure in the current version, exactly as a corrected input
    # would once the engine re-derived it.
    edited = copy.deepcopy(current.snapshot)
    edited["sections"][0]["rows"][0]["value"] = "999999.99"
    current.snapshot = edited
    db_session.commit()

    moved = version_chain.compare_versions(
        db_session, MAKER, SAMPLE_BANK_ID, superseded.id, current.id
    )
    assert moved.identical is False
    assert moved.changed_count == 1
    changed = moved.sections[0].lines[0]
    assert changed.change == "changed"
    assert changed.target_value == "999999.99"
    assert changed.delta is not None


def test_comparing_a_version_with_itself_yields_no_differences(
    db_session: Session,
) -> None:
    _superseded, current = _seed_two_versions(db_session)
    comparison = version_chain.compare_versions(
        db_session, MAKER, SAMPLE_BANK_ID, current.id, current.id
    )
    assert comparison.identical is True
    assert comparison.changed_count == 0
    assert comparison.added_count == 0
    assert comparison.removed_count == 0
    assert comparison.sections == []
    assert comparison.unchanged_section_count > 0
