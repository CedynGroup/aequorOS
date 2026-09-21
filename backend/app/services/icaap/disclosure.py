"""¶82: publishing the ICAAP outcome, and what may never be published.

¶82 asks an institution to publish the results of its ICAAP on its website and
to submit that publication to the supervisor. It does not say what "the
results" are, so the bank selects — from the SEALED report, never from live
data, because a disclosure that quotes a different number from the filing is
worse than no disclosure.

Three properties:

* **Nothing is public by default.** Every section starts unselected. A default
  that published would be a default that leaked.
* **Supervisory add-ons can never be published.** A capital add-on is the
  regulator's instruction to this bank; publishing it would disclose a
  supervisory measure the supervisor has not. Selecting a section that quotes
  one redacts the quotation and lists it in ``withheld``, so the narrative
  still reads and an auditor can see exactly what was removed.
* **Somebody other than the author approves it.** Four eyes, in the service and
  as a database CHECK, because what a bank publishes about its own capital
  adequacy is a statement it makes on a date.

Approval mints the ``ICAAP-DISCLOSURE`` package in the same transaction, built
from the source package's snapshot alone. Its ``package_id`` is write-once
(D-055): an approved disclosure cannot be repointed at a different report
afterwards.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import disclosure as domain
from app.domain.icaap.frameworks.schema import Framework
from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapDisclosure
from app.schemas.icaap import (
    IcaapDisclosureDecision,
    IcaapDisclosurePut,
    IcaapDisclosureRead,
    IcaapDisclosureSectionRead,
    IcaapDisclosureSubmit,
    IcaapDisclosureWithheldRead,
)
from app.services.audit import record_event
from app.services.icaap import guards

#: A disclosure quotes a report the Board has approved, never a draft.
DISCLOSABLE_STATUSES: frozenset[str] = frozenset({"board_approved", "submitted", "acknowledged"})
_LIVE = frozenset({"draft", "pending_approval", "approved", "published"})
#: The only disclosure an impersonated examiner may read. A draft names the
#: sections a bank is CONSIDERING publishing and what it is considering
#: withholding — a working proposal its own Board has not decided on, which is
#: precisely what the examiner branch exists not to serve. Once approved it is
#: a decision, and a supervisor reads decisions.
EXAMINER_VISIBLE: frozenset[str] = frozenset({"approved", "published"})


def _live_row(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> IcaapDisclosure | None:
    visible = EXAMINER_VISIBLE if access.examiner else _LIVE
    return db.scalar(
        select(IcaapDisclosure).where(
            IcaapDisclosure.organization_id == access.ctx.organization_id,
            IcaapDisclosure.cycle_id == cycle.id,
            IcaapDisclosure.status.in_(sorted(visible)),
        )
    )


def _package(db: Session, cycle: IcaapCycle) -> RegulatoryPackage | None:
    return None if cycle.package_id is None else db.get(RegulatoryPackage, cycle.package_id)


def _snapshot_icaap(package: RegulatoryPackage) -> dict[str, Any]:
    metadata = package.snapshot.get("metadata") or {}
    block = metadata.get("icaap")
    return block if isinstance(block, dict) else {}


def _never_public_ids(framework: Framework, block: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Block ids whose figures may never be published, by id.

    Two sources, deliberately: the framework's declared never-public TYPES, and
    anything sourced from the supervisory add-on register whatever its type.
    """
    forbidden_types = set(
        framework.disclosure.never_public_block_types if framework.disclosure else ()
    )
    found: dict[str, tuple[str, str]] = {}
    for entry in block.get("blocks") or []:
        if not isinstance(entry, dict):
            continue
        block_type = str(entry.get("block_type"))
        source_key = str(entry.get("source_key") or "")
        if (
            block_type in forbidden_types
            or entry.get("never_public")
            or source_key.startswith("bank_supervisory_addons")
        ):
            found[str(entry.get("block_id") or entry.get("block_key"))] = (
                str(entry.get("block_key")),
                block_type,
            )
    return found


def _selectable(framework: Framework, key: str) -> bool:
    return framework.has_section(key) and framework.section(key).public_disclosure


def _read(
    cycle: IcaapCycle,
    framework: Framework,
    row: IcaapDisclosure | None,
    *,
    available: bool,
    unavailable_reason: str | None,
) -> IcaapDisclosureRead:
    selected = set(row.selected_section_keys or []) if row is not None else set()
    return IcaapDisclosureRead(
        id=None if row is None else row.id,
        cycle_id=cycle.id,
        status=row.status if row is not None else "draft",  # pyright: ignore[reportArgumentType]
        source_package_id=None if row is None else row.source_package_id,
        package_id=None if row is None else row.package_id,
        available=available,
        unavailable_reason=unavailable_reason,
        sections=[
            IcaapDisclosureSectionRead(
                key=section.key,
                title=section.title,
                selected=section.key in selected,
                selectable=section.public_disclosure,
            )
            for section in framework.sections
        ],
        withheld=[
            IcaapDisclosureWithheldRead.model_validate(entry)
            for entry in (row.withheld or [])
            if isinstance(entry, dict)
        ]
        if row is not None
        else [],
        proposed_by=None if row is None else row.proposed_by,
        proposed_at=None if row is None else row.proposed_at,
        decided_by=None if row is None else row.decided_by,
        decided_at=None if row is None else row.decided_at,
        decision_reason=None if row is None else row.decision_reason,
        published_url=None if row is None else row.published_url,
        published_on=None if row is None else row.published_on,
    )


def get_disclosure(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapDisclosureRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    reason = _unavailable_reason(cycle, framework)
    return _read(
        cycle,
        framework,
        _live_row(db, access, cycle),
        available=reason is None,
        unavailable_reason=reason,
    )


def _unavailable_reason(cycle: IcaapCycle, framework: Framework) -> str | None:
    if framework.disclosure is None:
        return "This framework publishes no ICAAP disclosure requirement."
    if cycle.cycle_kind == "rehearsal":
        return "A rehearsal is never published."
    if cycle.status not in DISCLOSABLE_STATUSES:
        return "The Board has to approve this ICAAP before any of it can be published."
    return None


def _require_available(cycle: IcaapCycle, framework: Framework) -> None:
    reason = _unavailable_reason(cycle, framework)
    if reason is not None:
        raise guards.conflict("disclosure_unavailable", reason, status=cycle.status)


def put_disclosure(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapDisclosurePut
) -> IcaapDisclosureRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    framework = guards.require_framework(cycle)
    _require_available(cycle, framework)
    package = _package(db, cycle)
    if package is None:  # pragma: no cover - the CHECK forbids a sealed cycle without one
        raise guards.conflict("disclosure_unavailable", "This ICAAP has no sealed report.")

    unknown = [key for key in payload.selected_section_keys if not framework.has_section(key)]
    if unknown:
        raise guards.unprocessable(
            "section_unknown",
            "Those sections are not part of this framework.",
            sections=sorted(unknown),
        )
    not_publishable = [
        key for key in payload.selected_section_keys if not _selectable(framework, key)
    ]
    if not_publishable:
        raise guards.conflict(
            "section_not_publishable",
            "The framework marks these sections as not for publication: "
            f"{', '.join(sorted(not_publishable))}.",
            sections=sorted(not_publishable),
        )

    block = _snapshot_icaap(package)
    never_public = _never_public_ids(framework, block)
    withheld: list[dict[str, Any]] = []
    sections = {str(entry.get("key")): entry for entry in (block.get("sections") or [])}
    for key in sorted(set(payload.selected_section_keys)):
        entry = sections.get(key)
        doc = entry.get("doc") if isinstance(entry, dict) else None
        if not isinstance(doc, dict):
            continue
        redacted = domain.redact_section(key, doc, never_public=never_public)
        withheld.extend(
            {
                "section_key": item.section_key,
                "block_key": item.block_key,
                "block_type": item.block_type,
                "node_type": item.node_type,
                "fact_key": item.fact_key,
            }
            for item in redacted.withheld
        )

    row = _live_row(db, access, cycle)
    if row is not None and row.status not in {"draft"}:
        raise guards.conflict(
            "disclosure_not_editable",
            "This disclosure has been submitted for approval. Reject it first to "
            "change what is published.",
            status=row.status,
        )
    if row is None:
        row = IcaapDisclosure(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            source_package_id=package.id,
            status="draft",
            proposed_by=guards.actor_id(access),
        )
        db.add(row)
    row.selected_section_keys = sorted(set(payload.selected_section_keys))
    row.withheld = withheld
    row.proposed_by = guards.actor_id(access)
    row.proposed_at = utc_now()
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.disclosure.updated",
        entity_type="icaap_disclosure",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "selected": row.selected_section_keys,
            "withheld_count": len(withheld),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(cycle, framework, row, available=True, unavailable_reason=None)


def submit_disclosure(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapDisclosureSubmit
) -> IcaapDisclosureRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    framework = guards.require_framework(cycle)
    _require_available(cycle, framework)
    row = _live_row(db, access, cycle)
    if row is None or row.status != "draft":
        raise guards.conflict(
            "disclosure_not_editable",
            "There is no draft disclosure to submit for approval.",
            status=None if row is None else row.status,
        )
    if not row.selected_section_keys:
        raise guards.unprocessable(
            "disclosure_empty",
            "Choose at least one section to publish. Publishing nothing is not a ¶82 disclosure.",
        )
    row.status = "pending_approval"
    row.proposed_by = guards.actor_id(access)
    row.proposed_at = utc_now()
    record_event(
        db,
        access.ctx,
        event_type="icaap.disclosure.submitted",
        entity_type="icaap_disclosure",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(cycle, framework, row, available=True, unavailable_reason=None)


def approval_conditions(
    db: Session, access: IcaapAccess, cycle_id: UUID | None
) -> tuple[object, ...]:
    from app.core.authorization import ConditionCheck, ConditionKind  # noqa: PLC0415

    if cycle_id is None:
        return ()
    row = db.scalar(
        select(IcaapDisclosure).where(
            IcaapDisclosure.organization_id == access.ctx.organization_id,
            IcaapDisclosure.cycle_id == cycle_id,
            IcaapDisclosure.status == "pending_approval",
        )
    )
    distinct = row is None or row.proposed_by != access.ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "disclosure approver is distinct from whoever selected it"
                if distinct
                else "whoever chose what to publish cannot approve publishing it"
            ),
        ),
    )


def decide_disclosure(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapDisclosureDecision
) -> IcaapDisclosureRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    framework = guards.require_framework(cycle)
    _require_available(cycle, framework)
    row = _live_row(db, access, cycle)
    if row is None or row.status != "pending_approval":
        raise guards.conflict(
            "disclosure_not_pending",
            "There is no disclosure awaiting approval for this ICAAP.",
            status=None if row is None else row.status,
        )
    actor = guards.actor_id(access)
    if row.proposed_by == actor:
        raise guards.conflict(
            "maker_checker",
            "Whoever chose what to publish cannot approve publishing it. A different "
            "officer must decide.",
        )
    if payload.decision == "rejected":
        row.status = "rejected"
        row.decided_by = actor
        row.decided_at = utc_now()
        row.decision_reason = payload.reason
        record_event(
            db,
            access.ctx,
            event_type="icaap.disclosure.rejected",
            entity_type="icaap_disclosure",
            entity_id=row.id,
            details={"cycle_id": str(cycle.id), "reason": payload.reason},
        )
        db.commit()
        db.refresh(row)
        return _read(cycle, framework, row, available=True, unavailable_reason=None)

    source = db.get(RegulatoryPackage, row.source_package_id)
    if source is None:  # pragma: no cover - the FK forbids it
        raise guards.conflict("disclosure_unavailable", "The source report is missing.")
    try:
        package = _mint(db, access, cycle, row, source)
        row.status = "approved"
        row.decided_by = actor
        row.decided_at = utc_now()
        row.decision_reason = payload.reason
        row.package_id = package.id
        record_event(
            db,
            access.ctx,
            event_type="icaap.disclosure.approved",
            entity_type="icaap_disclosure",
            entity_id=row.id,
            details={
                "cycle_id": str(cycle.id),
                "package_id": str(package.id),
                "selected": row.selected_section_keys,
                "withheld_count": len(row.withheld or []),
                "reason": payload.reason,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return _read(cycle, framework, row, available=True, unavailable_reason=None)


def _mint(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    row: IcaapDisclosure,
    source: RegulatoryPackage,
) -> RegulatoryPackage:
    """Build the ICAAP-DISCLOSURE package from the SOURCE SNAPSHOT alone.

    Never from live data. A disclosure that quotes a figure the filing does not
    contain is a different statement about the bank's capital adequacy from the
    one the regulator holds.
    """
    from app.services.regulatory_reporting import generation  # noqa: PLC0415 - avoid a cycle

    framework = guards.require_framework(cycle)
    return_code = framework.filing.return_code_for("disclosure") if framework.filing else None
    if return_code is None:
        raise guards.conflict(
            "filing_not_available_for_framework",
            "This framework declares no disclosure return for the platform to file.",
        )
    block = _snapshot_icaap(source)
    never_public = _never_public_ids(framework, block)
    selected = set(row.selected_section_keys or [])
    sections: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    for entry in source.snapshot.get("sections") or []:
        code = str(entry.get("code"))
        if not code.startswith("s_") or code[2:] not in selected:
            continue
        sections.append(dict(entry))
    for entry in block.get("sections") or []:
        key = str(entry.get("key"))
        doc = entry.get("doc")
        if key not in selected or not isinstance(doc, dict):
            continue
        redacted = domain.redact_section(key, doc, never_public=never_public)
        withheld.extend(
            {
                "section_key": item.section_key,
                "block_key": item.block_key,
                "block_type": item.block_type,
                "node_type": item.node_type,
                "fact_key": item.fact_key,
            }
            for item in redacted.withheld
        )

    def _build() -> Any:
        return generation.FrozenSnapshot(
            snapshot={
                **{
                    key: value
                    for key, value in source.snapshot.items()
                    # ``provenance`` is dropped with the rest: it is the SOURCE
                    # return's authority record, naming ICAAP-REPORT. The mint
                    # restamps it for this return, so the published statement
                    # states its own authority rather than inheriting a claim
                    # about a document that was not published. What links the
                    # two is recorded below, as ``source_content_digest``.
                    if key not in {"sections", "totals", "metadata", "provenance"}
                },
                "return_code": return_code,
                "sections": sections,
                "totals": [],
                "metadata": {
                    "icaap_disclosure": {
                        "schema": "icaap-disclosure-snapshot-v1",
                        "source_package_id": str(source.id),
                        "source_version": source.version,
                        "source_content_digest": source.content_digest,
                        "cycle_id": str(cycle.id),
                        "selected_section_keys": sorted(selected),
                        "withheld": withheld,
                        "never_public_block_types": sorted(
                            framework.disclosure.never_public_block_types
                            if framework.disclosure
                            else ()
                        ),
                    }
                },
            },
            source_runs=list(source.source_runs or []),
        )

    return generation.generate_frozen_package(
        db,
        access.ctx,
        access.bank,
        return_code=return_code,
        reporting_date=source.reporting_date,
        build=_build,
        basis=source.basis,
        notes=f"¶82 disclosure of {source.return_code} version {source.version}.",
        commit=False,
    )


__all__ = [
    "DISCLOSABLE_STATUSES",
    "approval_conditions",
    "decide_disclosure",
    "get_disclosure",
    "put_disclosure",
    "submit_disclosure",
]
