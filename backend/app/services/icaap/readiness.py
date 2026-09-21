"""Readiness: gathering the cycle's state and turning findings into sentences.

The domain decides WHAT is outstanding; this module decides how to say it. The
regulator's name is data — it comes from the jurisdictions registry — so no copy
here names a country or a supervisor, and a Nigerian bank reads its own
regulator's name in the same sentence a Ghanaian bank reads its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import readiness as domain
from app.models.icaap import IcaapCycle
from app.schemas.icaap import (
    IcaapDeadlineStatusRead,
    IcaapReadinessItemRead,
    IcaapReadinessRead,
    IcaapSectionReadinessRead,
)
from app.services import jurisdictions
from app.services.icaap import (
    attachments,
    blocks,
    guards,
    parameters,
    readiness_p2,
    sections,
    serializers,
)

_MESSAGES: dict[str, str] = {
    "framework_digest_mismatch": (
        "The {framework} text has changed since this ICAAP was started. It must be "
        "moved onto the new version before it can be frozen."
    ),
    "framework_exposure_draft": (
        "{framework_title} is an exposure draft. {regulator} may change the text "
        "before it is final."
    ),
    "rehearsal_not_fileable": (
        "This is a rehearsal. It goes through the full process but is never filed and "
        "never satisfies a filing obligation."
    ),
    "filing_not_available_for_framework": (
        "{framework_title} is published for reference only; the platform cannot file "
        "to this regulator yet."
    ),
    "section_pending_primary_text": (
        "Checklist incomplete for {section_title} — pending {regulator} text."
    ),
    "section_not_committed": "{section_title} has no committed version yet.",
    "section_empty": "{section_title} is committed but says nothing.",
    "section_uncommitted_changes": (
        "{section_title} has edits that are not in a committed version."
    ),
    "requirement_open": "Not yet addressed: {requirement}",
    "block_referenced_unbound": "{block_title} is used in the report but not linked to data.",
    "block_stale": "{block_title} shows older figures than the engine now holds.",
    "block_as_of_mismatch": (
        "{block_title} shows figures for a different date than this ICAAP assesses."
    ),
    "block_source_missing": "{block_title} no longer has a source to read from.",
    "block_source_withdrawn": (
        "The data behind {block_title} was withdrawn. Refresh it or remove it from the report."
    ),
    "block_pinned": "{block_title} is deliberately kept at earlier figures: {reason}",
    "block_expected_absent": "{block_title} is expected in this section and has not been added.",
    "manual_table_without_evidence": (
        "{block_title} was filled in by hand and has no evidence attached."
    ),
    "attachment_missing": "{attachment_title} is required ({gate_phrase}).",
    "deadline_window_not_configured": (
        "The filing date is shown, but no amber window is configured for this "
        "institution, so the countdown is not colour-coded."
    ),
    "basis_companion_missing": (
        "This institution has subsidiaries, so a {other_basis} ICAAP is needed alongside "
        "this {basis} one."
    ),
    # --- IRRBB standardised framework (P5) --------------------------------
    "irrbb_interim_not_permitted": (
        "From {mandatory_from} the interest rate risk in the banking book figure must "
        "come from the standardised framework. This ICAAP still uses the interim "
        "method, which {regulator} no longer accepts for a reporting date this late."
    ),
    "irrbb_sf_required_missing": (
        "From {mandatory_from} this assessment must carry the standardised framework "
        "result for interest rate risk in the banking book. Run the framework for this "
        "reporting date and link it to the report."
    ),
    "irrbb_sf_refused": "{refusal_sentence}",
    "irrbb_sf_assumption_defaults": (
        "The standardised framework applied modelling defaults to {applied} positions "
        "because the book did not state their terms. Each one is listed with the figure "
        "and belongs in the assumptions the report discloses."
    ),
    "irrbb_sf_representative_parameters": (
        "The standardised framework figure rests on a calibration AequorOS chose rather "
        "than one the supervisor has published: {param_labels}. The report must say so."
    ),
    "irrbb_outlier": (
        "The standardised framework economic value measure is above the supervisory "
        "outlier threshold. The report has to address it."
    ),
}
#: P2's findings carry their own sentences; one catalogue, two authors.
_MESSAGES.update(readiness_p2.MESSAGES)

_PLACEHOLDER = re.compile(r"\{param:([a-z][a-z0-9_]{2,60})\}")
_GATE_PHRASE = {
    "freeze": "needed before freezing",
    "submission": "needed before submission",
    "optional": "optional",
    "per_block": "attached to a table",
}


def _message(
    item: domain.ReadinessItem,
    regulator: str,
    figures: Mapping[str, str],
    pending: frozenset[str],
) -> str:
    template = _MESSAGES.get(item.code, item.code.replace("_", " ").capitalize())
    params = {key: _fill_figures(value, figures, pending) for key, value in item.params.items()}
    params.setdefault("regulator", regulator)
    if "gate" in params:
        params["gate_phrase"] = _GATE_PHRASE.get(params["gate"], params["gate"])
    if "param_codes" in params:
        # A console row has two names: what a reader calls it, and what staff
        # search for. Print both — a bare code is jargon, and a bare label is
        # not enough to find the row that has to be changed.
        from app.domain.irr import standardised_params as sfp  # noqa: PLC0415

        params["param_labels"] = ", ".join(
            f"{sfp.PARAMETER_LABELS[code]} ({code})" if code in sfp.PARAMETER_LABELS else code
            for code in (entry.strip() for entry in params["param_codes"].split(","))
            if code
        )
    if "refusal_code" in params:
        # The engine's single refusal name (D-061) stays the machine token; the
        # sentence is rendered here, beside every other sentence in the product.
        from app.services.icaap.resolvers import irrbb_sf as sf_resolver  # noqa: PLC0415

        params["refusal_sentence"] = (
            sf_resolver.refusal_sentence(
                params["refusal_code"], params.get("refusal_message", "")
            )
            + " Quantify this risk another way, or agree the treatment of those positions "
            "with the supervisor."
        )
    try:
        return template.format(**params)
    except KeyError:  # pragma: no cover - a template referencing an absent param
        return template


def _fill_figures(value: str, figures: Mapping[str, str], pending: frozenset[str]) -> str:
    """A checklist item quotes governed figures; the reader sees the numbers.

    Leaving ``{param:...}`` in a sentence somebody has to act on would be worse
    than leaving the number out: it reads like a bug and says nothing.
    """
    if "{param:" not in value:
        return value
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        code = match.group(1)
        used.add(code)
        return figures.get(code, match.group(0))

    filled = _PLACEHOLDER.sub(replace, value)
    if used & pending:
        # The same note the section checklist appends, taken from the one
        # constant rather than repeated: two copies of a sentence a bank reads
        # are two sentences that can disagree.
        filled = f"{filled}{serializers.PENDING_FIGURE_NOTE}"
    return filled


def build_state(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, *, today: date | None = None
) -> tuple[domain.CycleState, bool]:
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read
    from app.services.icaap import sf_state  # noqa: PLC0415 - avoid an import cycle

    framework, digest_matches = guards.framework_for(cycle)
    companion = db.scalar(
        select(IcaapCycle)
        .where(
            IcaapCycle.organization_id == access.ctx.organization_id,
            IcaapCycle.bank_id == access.bank.id,
            IcaapCycle.cycle_kind == cycle.cycle_kind,
            IcaapCycle.as_of_date == cycle.as_of_date,
            IcaapCycle.basis != cycle.basis,
            IcaapCycle.status.notin_(["superseded", "archived"]),
        )
        .limit(1)
    )
    block_states = blocks.block_states(db, access, cycle)
    state = domain.CycleState(
        cycle_kind=cycle.cycle_kind,
        status=cycle.status,
        as_of=cycle.as_of_date,
        due_date=cycle.due_date,
        basis=cycle.basis,
        conditions=cycles_service.cycle_conditions(db, access, cycle),
        framework_digest_matches=digest_matches,
        companion_basis_cycle_exists=companion is not None,
        sections=sections.section_states(db, access, cycle, framework),
        blocks=block_states,
        active_attachment_counts=attachments.active_counts(db, access, cycle),
        # Dispatch-plane: the commencement row is resolved without entering the
        # session's consumption ledger, so a readiness check cannot land inside
        # the next run's parameter provenance (D-078).
        irrbb_sf=sf_state.build(
            db, access, cycle, framework, block_states, today=today or date.today()
        ),
    )
    return state, digest_matches


def get_readiness(
    db: Session, access: IcaapAccess, cycle_id: UUID, *, today: date | None = None
) -> IcaapReadinessRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    state, _digest_matches = build_state(db, access, cycle, today=today)
    # A display policy, not a regulatory floor: when no window is configured
    # readiness still answers, saying the countdown is not colour-coded (D-036).
    # Nothing is substituted — the alternative would be a platform default
    # printed as if somebody had chosen it.
    amber_days = parameters.try_resolve_int(db, access.bank, parameters.DEADLINE_AMBER_DAYS)
    as_of = today or date.today()
    report = domain.evaluate(framework, state, today=as_of, amber_days=amber_days)
    # The risk-and-capital half (P2) answers the same question about the
    # assessment behind the document, and appends its findings to the same
    # list, so a preparer sees one checklist and P3's freeze reads one answer.
    p2_items = readiness_p2.evaluate_p2(db, access, cycle, today=as_of)
    report = replace(
        report,
        items=(*report.items, *p2_items),
        ready_for_freeze=report.ready_for_freeze
        and not any(item.severity == "blocking" for item in p2_items),
    )
    regulator = jurisdictions.regulator_short(db, access.bank)
    # Only the codes a checklist SENTENCE quotes: the matrix bands and the
    # filing period are governed too, but they are structure, not prose.
    figures, pending = serializers.resolve_param_values(
        db,
        access.bank,
        frozenset(code for item in framework.all_items() for code in item.param_refs),
    )
    per_section: dict[str, dict[str, int]] = {}
    for item in report.items:
        key = item.params.get("section_key") or (item.ref if item.scope == "section" else None)
        if key is None:
            continue
        bucket = per_section.setdefault(key, {"blocking": 0, "warnings": 0})
        if item.severity == "blocking":
            bucket["blocking"] += 1
        elif item.severity == "warning":
            bucket["warnings"] += 1
    return IcaapReadinessRead(
        cycle_id=cycle.id,
        ready_for_freeze=report.ready_for_freeze,
        framework_status=framework.status,
        pending_primary_text_sections=list(report.pending_primary_text_sections),
        deadline=IcaapDeadlineStatusRead(
            due_date=report.deadline.due_date,
            days_remaining=report.deadline.days_remaining,
            rag=report.deadline.rag,
            basis=cycle.due_date_basis,  # pyright: ignore[reportArgumentType]
        ),
        items=[
            IcaapReadinessItemRead(
                code=item.code,
                severity=item.severity,
                scope=item.scope,
                ref=item.ref,
                message=_message(item, regulator, figures, pending),
            )
            for item in report.items
        ],
        sections=[
            IcaapSectionReadinessRead(
                key=key, blocking=counts["blocking"], warnings=counts["warnings"]
            )
            for key, counts in sorted(per_section.items())
        ],
        counts={key: dict(value) for key, value in report.section_counts.items()},
    )


__all__ = ["build_state", "get_readiness"]
