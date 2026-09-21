"""The seam between a frozen ICAAP package and its two artifacts.

Two things are protected here.

**The generic tabular exporter must never see an ICAAP package.** Its template
declares no section layouts, so it would render an empty workbook or a
line/cell listing and report a successful export of a document that does not
exist. S0 put a named 409 in the ``docx_working`` arm precisely because the kind
dispatch ends in a PDF fallback (D-056); this suite is what says the family hook
now answers first, so neither the refusal nor the fallback can be reached for an
ICAAP return.

**The snapshot reader is the whole contract.** Both renderers consume nothing
but ``from_snapshot``, so its tolerance and its refusals are what decide whether
a filed document can be re-rendered honestly in a year's time.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage
from app.services.icaap import filing_hooks
from app.services.icaap.render.from_snapshot import (
    NOT_RECORDED,
    SnapshotError,
    document_from_snapshot,
    filing_from_snapshot,
)
from app.services.regulatory_reporting import family_hooks
from app.services.regulatory_reporting.exports import (
    FAMILY_EXPORT_KINDS,
    icaap_docx,
    icaap_pdf,
)
from app.services.regulatory_reporting.family_hooks import FamilyHooks
from tests.services.icaap.filing_snapshot import CAPITAL_BLOCK_ID, FakePackage, snapshot

TWO_SIGNERS = ("preparer", "approver")

#: The hook's signature names a session, a tenant context and an ORM row. None
#: of the three is reached here — the policy lookup is patched and the reader
#: takes only the snapshot and a few package columns — so the arguments are cast
#: rather than a database being stood up to render a document from bytes.
NO_DB = cast("Session", None)
NO_CTX = cast("TenantContext", None)


def as_package(package: object) -> RegulatoryPackage:
    return cast("RegulatoryPackage", package)


class _Package(FakePackage):
    """A package the hook can read without a session."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.snapshot: dict[str, Any] = snapshot()
        self.bank_id = "BK-SAMP0001"


class _Policy:
    """A signing policy with exactly the slots a test names."""

    def __init__(self, *roles: str) -> None:
        self._roles = set(roles)

    def slot_for(self, role: str) -> object | None:
        return object() if role in self._roles else None


@pytest.fixture
def package() -> _Package:
    return _Package()


@pytest.fixture(autouse=True)
def _policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook resolves the policy in force; nothing else here touches a session."""
    monkeypatch.setattr(
        "app.services.attestation.workflow.package_policy",
        lambda db, ctx, pkg: _Policy("preparer", "approver"),
    )


# --------------------------------------------------------------------------
# The dispatch
# --------------------------------------------------------------------------
def test_the_icaap_family_declares_export_hooks_at_all() -> None:
    """Without this the generic path runs and D-056's refusal is what users see."""
    hooks = family_hooks.for_family("icaap")
    assert hooks is not None
    assert type(hooks).export is not FamilyHooks.export


def test_the_hook_renders_the_filing_pdf(package: _Package) -> None:
    payload, extension, stem = filing_hooks.HOOKS.export(
        NO_DB, NO_CTX, as_package(package), "pdf", None
    )
    assert payload.startswith(b"%PDF")
    assert extension == "pdf"
    assert stem == "ICAAP-REPORT"


def test_the_hook_renders_the_word_working_copy(package: _Package) -> None:
    payload, extension, stem = filing_hooks.HOOKS.export(
        NO_DB, NO_CTX, as_package(package), "docx_working", None
    )
    assert payload.startswith(b"PK")
    assert extension == "docx"
    # Never the filing's own object stem.
    assert stem == "ICAAP-REPORT.working"


def test_the_hook_refuses_an_unexpected_kind_instead_of_falling_through(
    package: _Package,
) -> None:
    """``NotImplementedError`` would hand the package back to the tabular path."""
    from fastapi import HTTPException  # noqa: PLC0415

    with pytest.raises(HTTPException) as refusal:
        filing_hooks.HOOKS.export(NO_DB, NO_CTX, as_package(package), "xlsx", None)
    assert refusal.value.status_code == 409
    detail = cast("dict[str, Any]", refusal.value.detail)
    assert detail["error_code"] == "export_kind_not_supported_for_return"


def test_the_only_kinds_the_family_admits_are_the_two_it_renders() -> None:
    assert FAMILY_EXPORT_KINDS["icaap"] == frozenset({"pdf", "docx_working"})


def test_the_board_slot_decides_how_many_blocks_the_hook_draws(
    package: _Package, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page follows the ceremony in force, not a constant in the renderer."""
    seen: list[tuple[str, ...]] = []
    original = icaap_pdf.render_filing_pdf

    def record(filing: Any) -> bytes:
        seen.append(filing.signing_order)
        return original(filing)

    monkeypatch.setattr(icaap_pdf, "render_filing_pdf", record)
    filing_hooks.HOOKS.export(NO_DB, NO_CTX, as_package(package), "pdf", None)
    monkeypatch.setattr(
        "app.services.attestation.workflow.package_policy",
        lambda db, ctx, pkg: _Policy("preparer", "approver", "board"),
    )
    filing_hooks.HOOKS.export(NO_DB, NO_CTX, as_package(package), "pdf", None)
    assert seen == [("preparer", "approver"), ("preparer", "approver", "board")]


def test_the_word_kind_the_hook_answers_to_is_the_one_every_layer_uses() -> None:
    from app.schemas.regulatory_reporting import ArtifactKind  # noqa: PLC0415
    from app.services.regulatory_reporting.exports import ExportKind  # noqa: PLC0415

    assert icaap_docx.ARTIFACT_KIND in ArtifactKind.__value__.__args__
    assert icaap_docx.ARTIFACT_KIND in ExportKind.__value__.__args__


# --------------------------------------------------------------------------
# The snapshot reader
# --------------------------------------------------------------------------
def test_a_snapshot_without_icaap_metadata_is_refused() -> None:
    with pytest.raises(SnapshotError):
        document_from_snapshot({"return_code": "BSD1"}, package=FakePackage())


def test_the_editor_s_block_reference_resolves_to_the_frozen_binding() -> None:
    """``dataBlock``/``factRef`` carry the block's UUID, so the snapshot must too."""
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    assert CAPITAL_BLOCK_ID in filing.document.blocks
    assert filing.document.fact(CAPITAL_BLOCK_ID, "total_capital") != "Not available"


def test_a_fact_the_binding_never_captured_reads_not_available_never_zero() -> None:
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    assert filing.document.fact(CAPITAL_BLOCK_ID, "nothing_here") == "Not available"


def test_a_pinned_block_says_so_and_gives_the_reason() -> None:
    payload = snapshot()
    block = payload["metadata"]["icaap"]["blocks"][0]
    block["pin_reason"] = "Held at the Board-approved figures."
    filing = filing_from_snapshot(payload, package=FakePackage())
    render = filing.document.blocks[CAPITAL_BLOCK_ID]
    assert render.status == "pinned"
    assert render.status_note is not None
    assert "Held at the Board-approved figures." in render.status_note


def test_an_unbound_block_is_reported_rather_than_dropped() -> None:
    payload = snapshot()
    payload["metadata"]["icaap"]["blocks"][0]["payload"] = None
    filing = filing_from_snapshot(payload, package=FakePackage())
    assert filing.document.blocks[CAPITAL_BLOCK_ID].status == "unbound"


def test_missing_keys_become_stated_absences_not_exceptions() -> None:
    """A sealed package must stay exportable years after the builder changed."""
    payload = snapshot()
    icaap = payload["metadata"]["icaap"]
    for key in ("framework", "institution_profile", "parameters", "stages", "annexes"):
        icaap.pop(key, None)
    package = FakePackage(content_digest=None, snapshot_sha256=None)
    filing = filing_from_snapshot(payload, package=package, signing_order=TWO_SIGNERS)
    assert filing.provenance.content_digest == NOT_RECORDED
    assert filing.provenance.snapshot_sha256 == NOT_RECORDED
    assert filing.document.framework_code == NOT_RECORDED
    assert icaap_pdf.render_filing_pdf(filing).startswith(b"%PDF")


def test_the_signing_order_is_the_callers_and_never_the_snapshots() -> None:
    """The rules must match the fields the ceremony in force will create."""
    payload = snapshot()
    payload["metadata"]["icaap"]["filing"]["signing_order"] = ["preparer", "approver", "board"]
    filing = filing_from_snapshot(payload, package=FakePackage(), signing_order=TWO_SIGNERS)
    assert filing.signing_order == TWO_SIGNERS
    assert [slot.role for slot in filing.provenance.signature_slots] == list(TWO_SIGNERS)


def test_the_review_record_is_one_row_per_decision_not_per_stage() -> None:
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    assert [stage.decided_by_name for stage in filing.provenance.stages] == [
        "Ama Mensah",
        "Efua Asante",
    ]


def test_a_parameter_with_no_governed_row_is_carried_as_unresolved() -> None:
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    unresolved = [entry for entry in filing.provenance.parameters if entry.value == "Not resolved"]
    assert [entry.code for entry in unresolved] == ["icaap_stress_horizon_years_min"]


def test_the_ai_assisted_count_is_summed_across_sections() -> None:
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    assert filing.provenance.ai_assisted_paragraphs == 2


def test_a_filed_report_states_no_readiness() -> None:
    """Readiness is what had to clear before the freeze, and the freeze happened."""
    filing = filing_from_snapshot(snapshot(), package=FakePackage())
    assert filing.document.readiness_summary == ()
