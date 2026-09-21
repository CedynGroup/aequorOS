"""What the ICAAP family adds to the generic regulatory plane.

The generic plane owns what a package IS. This module owns the handful of
things that are true of an ICAAP filing and of nothing else, and it is the ONLY
place the generic services learn them — ``family_hooks.HOOK_MODULES`` resolves
it lazily, and a tree without it simply has no ICAAP rules rather than a broken
import.

Four rules live here.

**The Board resolution is required by the FRAMEWORK, not by the signing policy
(D-031, audit M11).** ¶71 asks for the resolutions to accompany the submission.
Relaxing the Board's SIGNATURE slot says something about signatures; it has
never said anything about which documents go with a filing, and letting a
signing switch exempt a regulator document would turn signing infrastructure
into a regulatory control. So the requirement is read from the frozen
snapshot's own manifest and is not relaxable.

**Only the cycle's current package may be submitted.** A post-freeze send-back
unlinks a package from its cycle; that package stays readable as history but is
no longer the filing, and must not be able to reach a channel.

**The Board's approval moves the cycle, once.** When the final required
signature lands the cycle goes ``frozen → board_approved`` and an ``attested``
decision is written naming the signer. Where the policy has no Board slot, the
Board's approval is evidenced by the filed resolution instead, and that
decision is written at submission.

**The package's status is the cycle's status.** Submission, acknowledgement and
supersession each move the cycle, so the workspace and the filing can never
disagree about where the report is.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapCycleStage, IcaapStageDecision
from app.services.regulatory_reporting.family_hooks import FamilyHooks

FAMILY = "icaap"


def cycle_for_package(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> IcaapCycle | None:
    """The cycle this package is the CURRENT filing of, if it still is one."""
    return db.scalar(
        select(IcaapCycle).where(
            IcaapCycle.organization_id == ctx.organization_id,
            IcaapCycle.package_id == package.id,
        )
    )


def _snapshot_icaap(package: RegulatoryPackage) -> dict[str, Any]:
    metadata = package.snapshot.get("metadata") or {}
    block = metadata.get("icaap")
    return block if isinstance(block, dict) else {}


def _attest_stage(db: Session, ctx: TenantContext, cycle: IcaapCycle) -> IcaapCycleStage | None:
    return db.scalar(
        select(IcaapCycleStage)
        .where(
            IcaapCycleStage.organization_id == ctx.organization_id,
            IcaapCycleStage.cycle_id == cycle.id,
            IcaapCycleStage.decision_kind == "attest",
        )
        .order_by(IcaapCycleStage.seq.desc())
        .limit(1)
    )


def _conflict(code: str, message: str, **extra: Any) -> Exception:
    from fastapi import HTTPException, status  # noqa: PLC0415 - one import per refusal site

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": code, "message": message, **extra},
    )


class IcaapFamilyHooks(FamilyHooks):
    family = FAMILY

    # --- submission ------------------------------------------------------
    def required_attachments(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> dict[str, int]:
        """The framework's own document requirements, as frozen at the freeze.

        Read from the SNAPSHOT, not from the live framework registry: what the
        filing must carry was settled when the report was sealed, and a
        framework version published afterwards must not change the obligations
        of a report already with the Board.
        """
        _ = (db, ctx)
        required: dict[str, int] = {}
        for entry in _snapshot_icaap(package).get("attachment_requirements") or []:
            if not isinstance(entry, dict) or not entry.get("applies", True):
                continue
            if entry.get("gate") not in {"freeze", "submission"}:
                continue
            minimum = entry.get("min_count") or 0
            if minimum > 0:
                required[str(entry.get("kind"))] = int(minimum)
        return required

    def ensure_submittable(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: Any
    ) -> None:
        cycle = cycle_for_package(db, ctx, package)
        if cycle is None:
            raise _conflict(
                "icaap_package_not_current",
                "This version of the ICAAP report is no longer the one the workspace "
                "holds. It was sent back for correction, and the corrected version is "
                "what gets filed.",
            )
        if cycle.frozen_at is None:  # pragma: no cover - the CHECK forbids it
            raise _conflict("icaap_cycle_not_frozen", "This ICAAP has not been frozen.")
        board_slot = getattr(policy, "slot_for", lambda _role: None)("board")
        if board_slot is not None and cycle.board_approved_at is None:
            raise _conflict(
                "board_approval_outstanding",
                "The Board has not signed this ICAAP report yet.",
            )

    def ensure_voidable(self, db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
        cycle = cycle_for_package(db, ctx, package)
        if cycle is not None and cycle.board_approved_at is not None:
            raise _conflict(
                "icaap_use_return_to_stage",
                "The Board has approved this ICAAP. Send it back to a review stage "
                "instead of voiding the signatures — the send-back is recorded, and "
                "silently discarding a Board approval is not.",
            )

    # --- validation ------------------------------------------------------
    def validation_findings(self, db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
        from app.services.icaap import validation_rules  # noqa: PLC0415 - lazy, one direction

        return validation_rules.findings(db, package)

    # --- lifecycle -------------------------------------------------------
    def on_transition(
        self,
        db: Session,
        ctx: TenantContext,
        package: RegulatoryPackage,
        *,
        previous: str,
        new_status: str,
    ) -> None:
        _ = previous
        cycle = cycle_for_package(db, ctx, package)
        if cycle is None:
            return
        if new_status == "submitted" and cycle.status in {"frozen", "board_approved"}:
            self._record_resolution_attestation(db, ctx, cycle)
            cycle.status = "submitted"
            cycle.submitted_at = utc_now()
        elif new_status == "acknowledged" and cycle.status == "submitted":
            cycle.status = "acknowledged"
            cycle.acknowledged_at = utc_now()
        # ``rejected`` deliberately leaves the cycle ``submitted``: the filing
        # WAS made, and the post-freeze send-back is how a bank responds.

    def _record_resolution_attestation(
        self, db: Session, ctx: TenantContext, cycle: IcaapCycle
    ) -> None:
        """Where the Board did not sign the PDF, the filed resolution is the evidence.

        D-043 ships the Board signature slot OFF by default, so for most banks
        the Board's approval reaches the record here rather than through a
        signature — against the resolution that ¶71 requires with the filing.
        """
        if cycle.board_approved_at is not None:
            return
        stage = _attest_stage(db, ctx, cycle)
        if stage is None:
            return
        existing = db.scalar(
            select(IcaapStageDecision).where(
                IcaapStageDecision.organization_id == ctx.organization_id,
                IcaapStageDecision.cycle_id == cycle.id,
                IcaapStageDecision.decision == "attested_by_resolution",
            )
        )
        if existing is not None:
            return
        actor = ctx.actor_user_id
        if actor is None:  # pragma: no cover - refused upstream
            return
        digest = _snapshot_review_digest(db, cycle)
        if digest is None:  # pragma: no cover - a frozen cycle always has one
            return
        from app.services.attestation import identity  # noqa: PLC0415 - avoid an import cycle

        name, title = identity.resolve_signer_display(db, ctx, actor)
        db.add(
            IcaapStageDecision(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                stage_seq=stage.seq,
                stage_key=stage.stage_key,
                round=cycle.round,
                decision="attested_by_resolution",
                review_digest=digest,
                package_id=cycle.package_id,
                comment="Board approval evidenced by the resolution filed with this report.",
                decided_by=actor,
                decided_by_name=name or "Name not recorded",
                officer_title=title,
            )
        )

    def on_fully_certified(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> None:
        """The last required signature landed: the cycle is Board approved."""
        from app.services.attestation import workflow as attestation_workflow  # noqa: PLC0415

        cycle = cycle_for_package(db, ctx, package)
        if cycle is None or cycle.status != "frozen":
            return
        policy = attestation_workflow.package_policy(db, ctx, package)
        if policy.slot_for("board") is None:
            # No Board slot: the Board's approval is the filed resolution, and
            # it is recorded at submission rather than here. Moving the cycle
            # on a preparer + approver signature would claim a Board approval
            # nobody gave.
            return
        signatures = attestation_workflow.current_signatures(db, ctx, package)
        board = next((row for row in signatures if row.signing_role == "board"), None)
        if board is None:  # pragma: no cover - full certification implies the slot
            return
        cycle.status = "board_approved"
        cycle.board_approved_at = utc_now()
        stage = _attest_stage(db, ctx, cycle)
        digest = _snapshot_review_digest(db, cycle)
        if stage is None or digest is None:  # pragma: no cover - a frozen cycle has both
            return
        db.add(
            IcaapStageDecision(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                stage_seq=stage.seq,
                stage_key=stage.stage_key,
                round=cycle.round,
                decision="attested",
                review_digest=digest,
                package_id=package.id,
                signature_id=board.id,
                comment="Board approval recorded by signature on the frozen report.",
                decided_by=board.signer_user_id,
                decided_by_name=board.signer_display_name or "Name not recorded",
                officer_title=board.officer_title,
            )
        )

    def on_package_superseded(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> None:
        """A regenerated version replaced this one: its cycle is superseded."""
        cycle = cycle_for_package(db, ctx, package)
        if cycle is None or cycle.superseded_at is not None:
            return
        cycle.status = "superseded"
        cycle.superseded_at = utc_now()

    # --- export ----------------------------------------------------------
    def export(  # noqa: PLR0913 - the generic seam's own signature
        self,
        db: Session,
        ctx: TenantContext,
        package: RegulatoryPackage,
        kind: str,
        bank: Any,
    ) -> tuple[bytes, str, str]:
        """The ICAAP report's own two artifacts, rendered from the frozen snapshot.

        The generic tabular exporter must never see an ICAAP package: its
        template declares no section layouts, so it would produce an empty
        workbook or a line/cell listing and report a successful export of a
        document that does not exist. This hook is what stops that, and the
        refusal at the end of it is deliberate — returning ``NotImplementedError``
        for an unexpected kind would hand the package back to exactly that path.

        ``signing_order`` is resolved from the policy in force rather than from
        the snapshot, because the attestation page's ruled blocks have to match
        the AcroForm fields ``pdf_signing`` is about to create for THIS ceremony.
        The Board slot ships disabled (D-043), so the default is two blocks.
        """
        from app.services.attestation import layouts  # noqa: PLC0415 - avoid an import cycle
        from app.services.attestation.workflow import package_policy  # noqa: PLC0415
        from app.services.icaap.render.from_snapshot import (  # noqa: PLC0415
            SnapshotPackage,
            filing_from_snapshot,
        )
        from app.services.regulatory_reporting.exports import (  # noqa: PLC0415 - reportlab is heavy
            icaap_docx,
            icaap_pdf,
        )

        _ = bank
        filing = filing_from_snapshot(
            package.snapshot,
            # The reader declares the columns it needs as a protocol so it can
            # stay out of the model layer; SQLAlchemy's ``Mapped`` descriptors
            # satisfy it at runtime but not to a static checker.
            package=cast("SnapshotPackage", package),
            signing_order=layouts.signing_order_for(
                package.return_family, package_policy(db, ctx, package)
            ),
        )
        if kind == "pdf":
            return icaap_pdf.render_filing_pdf(filing), "pdf", package.return_code
        if kind == icaap_docx.ARTIFACT_KIND:
            return (
                icaap_docx.render_working_docx(filing),
                icaap_docx.EXTENSION,
                icaap_docx.file_stem(package.return_code),
            )
        raise _conflict(
            "export_kind_not_supported_for_return",
            f"'{package.return_code}' is a document, not a workbook: it exports as "
            "pdf, docx_working.",
            allowed_kinds=["docx_working", "pdf"],
        )


def _snapshot_review_digest(db: Session, cycle: IcaapCycle) -> str | None:
    """The digest the frozen decision recorded — never recomputed from live rows.

    ``None`` when there is no frozen decision to quote, and the caller then
    writes NO decision. A placeholder digest would be a decision claiming to
    attest to something, against a value that attests to nothing.
    """
    row = db.scalar(
        select(IcaapStageDecision)
        .where(
            IcaapStageDecision.organization_id == cycle.organization_id,
            IcaapStageDecision.cycle_id == cycle.id,
            IcaapStageDecision.decision == "frozen",
        )
        .order_by(IcaapStageDecision.created_at.desc())
        .limit(1)
    )
    return None if row is None else row.review_digest


HOOKS = IcaapFamilyHooks()

__all__ = ["FAMILY", "HOOKS", "IcaapFamilyHooks", "cycle_for_package"]
