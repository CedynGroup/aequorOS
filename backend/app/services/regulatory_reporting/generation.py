"""Package generation (docs/regulatory_reporting.md §5, ``generation.py``).

Generators pull ONLY from existing computed state — module previews and
succeeded ``RegulatoryRun`` snapshots — and never recompute engine outputs.
Every generated snapshot embeds the values that will be exported plus
``source_runs`` ({module, run_id, input_hash, engine_version}) so each number
traces back through the lineage substrate.

``generate_package`` mints the immutable package row: status ``generated``,
version = prior version + 1, and the prior current version (if any) flips to
``superseded`` in the same transaction. Regeneration never mutates a snapshot.

Snapshot shape (``regulatory-package-v1``): sections are
``{code, title, optional, rows: [{code, description, value, ...}], total}``
where a ``total`` carrying ``equals_sum_of_rows=True`` declares that the
validation pipeline must cross-foot it against the row values. Top-level
``totals`` rows are the stable headline figures the prior-period movement
check compares across reporting dates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import observability
from app.domain.policy import Direction, direction_for
from app.models import (
    Bank,
    BankReportingPeriod,
    InstitutionProfile,
    RegulatoryMetricResult,
    RegulatoryPackage,
    RegulatoryRun,
    User,
)
from app.schemas.regulatory_liquidity import Bsd3SummaryRowRead
from app.schemas.regulatory_reporting import RegulatoryPackageCreate, RegulatoryPackageRead
from app.services import (
    filing_reconciliation,
    regulatory_capital,
    regulatory_forecasting,
    regulatory_liquidity,
    withdrawal_impact,
)
from app.services.attestation import digests, register_state
from app.services.audit import record_event
from app.services.regulatory_reporting import family_hooks
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_snapshot_for_reporting_date,
    read_package,
    require_actor,
)
from app.services.regulatory_reporting.eligibility import resolve_eligibility
from app.services.regulatory_reporting.provenance import (
    UNCLASSIFIED_STATUS,
    ReportAuthority,
    build_engine_provenance,
    compliance_verdict_authority,
)
from app.services.regulatory_reporting.provenance import (
    calculation_provenance as _calculation_provenance,
)
from app.services.regulatory_reporting.provenance import (
    source_run_entry as _calculation_source_run_entry,
)
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition
from app.services.regulatory_reporting.templates import (
    APPENDIX2_PILLAR2_RISKS,
    NOT_MODELLED,
    NOT_STATED,
    TEMPLATE_REVISIONS,
    appendix2_exposure_class_label,
    appendix2_risk_driver_label,
)

#: Observability (docs/sdi.md §19) — the runtime-log counterpart to the persistent
#: ``regulatory_package.generated`` audit event, tagged with the institution class so
#: SDI (s.29 / NBFI) runs are distinguishable from bank (Basel) runs in the log stream.
logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = "regulatory-package-v1"
BASELINE_SCENARIO = "baseline"


def snapshot_content_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 over the canonical-JSON snapshot — the package's content seal.

    Value-based and key-sorted, mirroring the regulatory ``input_hash``
    discipline: identical content always hashes identically regardless of
    insertion order.
    """
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


MODULE_LIQUIDITY = "liquidity"
MODULE_CAPITAL = "capital"
MODULE_IRR = "irr"
MODULE_FX = "fx"
MODULE_FORECAST = "forecast"
# The ICAAP data companion consumes 5-year forecast runs only. Desk runs may
# carry other horizons (persisted as ``inputs.horizon_years``; absent == 5),
# and must never displace the regulatory 5-year projection here. The rule is
# ``regulatory_forecasting.regulatory_horizon_clause`` — shared with the capital
# plan, so the two ICAAP consumers can never disagree about which run counts.

_FORECAST_SUMMARY_FIELDS = (
    "avg_roe_pct",
    "year5_car_pct",
    "year5_lcr_pct",
    "year5_nsfr_pct",
    "cumulative_net_income",
    "min_car_pct",
    "min_lcr_pct",
    "min_nsfr_pct",
)


@dataclass(frozen=True)
class GeneratedReturn:
    """One generator output: the export-ready snapshot + its source runs.

    ``source_runs`` stays exactly what it has always been — the sealed
    ``RegulatoryRun`` lineage, empty for returns no engine produced. What the
    forensic audit (§8, §10 item 3) required is that an EMPTY list stop being a
    silent hole: ``generate_package`` stamps
    :func:`~app.services.regulatory_reporting.provenance` onto every snapshot,
    and the template-authoritative case says so in words.
    """

    snapshot: dict[str, Any]
    source_runs: list[dict[str, Any]]


#: Refusals whose ``detail`` carries no ``error_code`` of its own, keyed by the
#: HTTP status the mint site raises. Keeps the observability reason code stable
#: and greppable even where the refusal only carries prose.
_REFUSAL_REASON_BY_STATUS: dict[int, str] = {
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "unprocessable",
}


def _refusal_reason(exc: HTTPException) -> str:
    """The refusal's own ``error_code`` where it has one, else a status-derived code."""
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("error_code")
        if isinstance(code, str) and code:
            return code
    # A typed refusal (``FilingBlockedError``) carries prose in ``detail`` and
    # its code as an attribute, so the reason stays precise instead of "conflict".
    typed = getattr(exc, "error_code", None)
    if isinstance(typed, str) and typed:
        return typed
    return _REFUSAL_REASON_BY_STATUS.get(exc.status_code, f"http_{exc.status_code}")


def generate_package(
    db: Session, ctx: TenantContext, bank_id: str, payload: RegulatoryPackageCreate
) -> RegulatoryPackageRead:
    """Mint the immutable package row, reporting every refusal (audit: observability).

    Thin wrapper over :func:`_generate_package` for ONE reason: a generation
    failure used to leave no trace anywhere. Every refusal below — an unregistered
    return code, an ineligible institution class, a pending BoG template, a
    missing baseline run, an acknowledged return without a resubmission grant —
    raised a bare ``HTTPException`` and returned a 4xx to one client. No audit
    event is written on refusal by design (no package row exists to attach one
    to), which is precisely why the structured log line is the only record there
    can be, and why it is emitted HERE, at the single mint site, rather than at
    each of the raise sites.

    The emit never changes the outcome: ``observability.emit`` swallows its own
    failures and the original exception is always re-raised unchanged.
    """
    try:
        return _generate_package(db, ctx, bank_id, payload)
    except HTTPException as exc:
        observability.package_failed(
            reason=_refusal_reason(exc),
            status_code=exc.status_code,
            return_code=payload.return_code,
            reporting_date=payload.reporting_date.isoformat(),
            basis=payload.basis,
            bank_id=bank_id,
            organization_id=ctx.organization_id,
        )
        raise
    except Exception as exc:
        # An unexpected failure is a 500 to the client and must be at least as
        # visible as a refusal — the same condition code, raised severity.
        observability.package_failed(
            reason="unhandled_exception",
            severity="error",
            exception_type=type(exc).__name__,
            return_code=payload.return_code,
            reporting_date=payload.reporting_date.isoformat(),
            basis=payload.basis,
            bank_id=bank_id,
            organization_id=ctx.organization_id,
        )
        raise


FREEZE_ONLY_FAMILIES: frozenset[str] = frozenset({"icaap"})


def _refuse_freeze_only_family(definition: ReturnDefinition) -> None:
    """Refuse the generic mint for a return whose package comes from a freeze.

    An ICAAP package is the evidence that a particular version of a particular
    cycle was reviewed and sealed. Minting one from this endpoint would produce
    a filing document with no review chain behind it and nothing to bind the
    signatures to, which is the whole property the ICAAP filing plane exists to
    guarantee.
    """
    if definition.family not in FREEZE_ONLY_FAMILIES:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "icaap_generated_by_freeze",
            "message": (
                f"'{definition.code}' is not generated here. An ICAAP filing exists "
                "because an ICAAP cycle was reviewed and frozen \u2014 open the ICAAP "
                "workspace and freeze the cycle, which mints this package and binds "
                "it to the review that approved it."
            ),
            "return_code": definition.code,
            "return_family": definition.family,
        },
    )


def _supersede_prior(  # noqa: PLR0913 - the supersession key is its named parts
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    definition: ReturnDefinition,
    *,
    reporting_date: date,
    basis: str,
    rehearsal: bool = False,
) -> tuple[RegulatoryPackage | None, int | None, Any]:
    """Retire the current version for this (return, date, basis) and number the next.

    Supersession and versioning are per-basis: solo and consolidated are
    independent current-version chains for the same (return, reporting date).
    Shared by the generator path and the ICAAP freeze path so the two cannot
    drift into two notions of "which version is current".

    A REHEARSAL is a third, separate chain (D-029 / D-068). It must never retire
    a real version, and a real version must never retire a rehearsal: they are
    answers to different questions, and either direction would let a dry run
    change the status of a filing (or a filing erase the evidence of a dry run).
    The row-level CHECK can only express the first half — ``supersedes_id IS
    NULL`` on a rehearsal — because the second reads another row. This is where
    the other half lives.
    """
    prior_current = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == reporting_date,
            RegulatoryPackage.basis == basis,
            RegulatoryPackage.status != "superseded",
            RegulatoryPackage.is_rehearsal == rehearsal,
        )
    )
    # ORASS parity: an ACKNOWLEDGED return is final at the regulator; a
    # correcting version exists only under a granted resubmission request
    # (LRT guide §5.3). The grant is one-shot — consumed by this regeneration.
    resubmission_authorization = None
    if prior_current is not None and prior_current.status == "acknowledged":
        from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415
            granted_unconsumed_resubmission,
        )

        resubmission_authorization = granted_unconsumed_resubmission(db, prior_current)
        if resubmission_authorization is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This return was acknowledged by the regulator; regenerating "
                    "it requires a GRANTED resubmission request (Request "
                    "Resubmission) on the acknowledged package first."
                ),
            )
    prior_version = db.scalar(
        select(RegulatoryPackage.version)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == reporting_date,
            RegulatoryPackage.basis == basis,
            RegulatoryPackage.is_rehearsal == rehearsal,
        )
        .order_by(RegulatoryPackage.version.desc())
        .limit(1)
    )
    if prior_current is not None:
        prior_current.status = "superseded"
        record_event(
            db,
            ctx,
            event_type="regulatory_package.superseded",
            entity_type="regulatory_package",
            entity_id=prior_current.id,
            details={
                "return_code": definition.code,
                "reporting_date": reporting_date.isoformat(),
                "version": prior_current.version,
            },
        )
        # The family's own hook runs on a DIRECT supersession too, not only on
        # a table transition: a superseding regeneration never passes through
        # ``workflow.transition`` (P3-DESIGN §5.4).
        family_hooks.on_package_superseded(db, ctx, prior_current)
        db.flush()
    return prior_current, prior_version, resubmission_authorization


def _generate_package(
    db: Session, ctx: TenantContext, bank_id: str, payload: RegulatoryPackageCreate
) -> RegulatoryPackageRead:
    actor_user_id = require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    definition = REGISTRY.get(payload.return_code)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Return code '{payload.return_code}' is not registered. "
                "List the available templates via the return-template endpoint."
            ),
        )
    # The family gate, BEFORE eligibility. A return whose packages exist only
    # because a workspace cycle was frozen cannot be minted here at ANY date, so
    # answering "not eligible on this reporting date" would be a true sentence
    # about the wrong question and would send an operator hunting for a date
    # that does not exist. Said once, structurally, at the only generic mint
    # site (ICAAP P3 §4.2).
    _refuse_freeze_only_family(definition)
    # Server-side eligibility (audit ARCH-8) through the SINGLE authority the
    # reporting calendar also consumes, so the two surfaces cannot disagree. It
    # is evaluated HERE, and again at the OTHER mint site
    # (``generate_frozen_package``, with a documented ``ignore``), which is what
    # makes an ineligible return structurally impossible to generate: an SDI
    # POSTing a bank-only BSD code directly never reaches a generator. Every
    # dimension — institution class, jurisdiction, regulator, cadence anchor,
    # effective date — is named on the 403 rather than a single opaque refusal.
    eligibility = resolve_eligibility(db, ctx, bank, as_of=payload.reporting_date)
    bank_class = eligibility.institution_class
    eligibility.require(definition, reporting_date=payload.reporting_date)
    # The figures are the figures AS OF the regulator's reporting date, for every
    # cadence — no "nearest earlier book" fallback (see the resolver's docstring
    # for the daily-return fail-open it replaces).
    period = get_snapshot_for_reporting_date(
        db,
        ctx,
        bank,
        payload.reporting_date,
        return_code=definition.code,
        frequency=definition.frequency,
    )

    # The data-integrity gate (audit P0-10 / 2026-08-22 D-2), evaluated HERE for
    # the same reason eligibility is, so a return built on a book that does not
    # balance is structurally impossible rather than merely discouraged. It runs
    # before any generator so a refusal leaves nothing behind, and it raises
    # ``FilingBlockedError`` — a 409 whose detail names the gap, the governed
    # tolerance and its source. The frozen mint site's caller holds this one
    # (``icaap/freeze.py::_assert_reconciled``, D-069): either owner may hold a
    # gate, NEITHER may drop it.
    filing_reconciliation.assert_filing_reconciled(
        db,
        ctx,
        bank,
        as_of=period.period_end,
        period_id=period.id,
        purpose="package_generation",
    )

    generated = _GENERATORS[definition.generator](db, ctx, bank, period, definition)

    # The withdrawn-evidence gate (audit 2026-08-22 D-12), on the same line and
    # for the same reason as the two gates above: a return bound to a run that
    # was sealed on canonical rows since retired under two-officer withdrawal is
    # structurally impossible rather than merely unlikely. The sealed runs
    # themselves are never touched — they remain the faithful record of the book
    # as it stood; it is the FILING that is refused, with a 409 naming every
    # withdrawal behind the refusal. ``generate_frozen_package`` runs the same
    # gate over ITS lineage (D-069), so neither mint site can lose it.
    _assert_evidence_current(db, ctx, generated.source_runs)

    _enrich_institution_block(db, ctx, bank, generated.snapshot)
    _stamp_basis(generated.snapshot, payload.basis)
    _stamp_template_revision(definition, generated.snapshot)
    _stamp_provenance(
        db,
        ctx,
        bank,
        definition,
        reporting_date=payload.reporting_date,
        snapshot=generated.snapshot,
        source_runs=generated.source_runs,
    )
    _apply_prior_period_comparative(db, ctx, bank, definition, payload, generated.snapshot)

    prior_current, prior_version, resubmission_authorization = _supersede_prior(
        db,
        ctx,
        bank,
        definition,
        reporting_date=payload.reporting_date,
        basis=payload.basis,
    )

    # Attestation binding, sealed with the snapshot (docs/attestation_esignature.md
    # §3.1). Every return gets a content_digest — snapshot_sha256 embeds
    # metadata.generated_at and therefore seals a VERSION, not content (gap G13).
    # Packs that bind no engine run additionally get the master-data provenance
    # analogue, without which a signature over them would bind figures with
    # nothing behind them (gap G16).
    register_digest = (
        digests.register_state_digest(register_state.register_state_rows(db, ctx, bank.id))
        if not generated.source_runs
        else None
    )
    package = RegulatoryPackage(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        return_family=definition.family,
        return_code=definition.code,
        reporting_date=payload.reporting_date,
        frequency=definition.frequency,
        basis=payload.basis,
        status="generated",
        version=(prior_version or 0) + 1,
        supersedes_id=prior_current.id if prior_current is not None else None,
        snapshot=generated.snapshot,
        source_runs=generated.source_runs,
        validation_report=None,
        generated_by=actor_user_id,
        generated_at=datetime.now(UTC),
        notes=payload.notes,
        snapshot_sha256=snapshot_content_hash(generated.snapshot),
        content_digest=digests.content_digest(generated.snapshot),
        register_state_digest=register_digest,
    )
    db.add(package)
    db.flush()
    if resubmission_authorization is not None:
        resubmission_authorization.consumed_by_package_id = package.id
    logger.info(
        "regulatory_package.generated return_code=%s family=%s institution_class=%s "
        "bank=%s org=%s reporting_date=%s basis=%s version=%s",
        definition.code,
        definition.family,
        bank_class,
        bank.id,
        ctx.organization_id,
        payload.reporting_date.isoformat(),
        payload.basis,
        package.version,
    )
    record_event(
        db,
        ctx,
        event_type="regulatory_package.generated",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "bank_id": str(bank.id),
            "return_code": definition.code,
            "return_family": definition.family,
            "reporting_date": payload.reporting_date.isoformat(),
            "basis": payload.basis,
            "version": package.version,
            "supersedes_id": (str(prior_current.id) if prior_current is not None else None),
            "source_runs": [entry["run_id"] for entry in generated.source_runs],
            "content_digest": package.content_digest,
            "register_state_digest": package.register_state_digest,
        },
    )
    db.commit()
    return read_package(db, package)


@dataclass(frozen=True)
class FrozenSnapshot:
    """What a family's own freeze path hands the package minter."""

    snapshot: dict[str, Any]
    source_runs: list[dict[str, Any]]


def _is_rehearsal(snapshot: dict[str, Any]) -> bool:
    """Is this frozen snapshot a DRY RUN?

    Read from the snapshot's own cycle block rather than taken on trust from the
    caller, so the package row and the rendered document cannot disagree about
    what the reader is looking at — the renderers derive the watermark from the
    same field (``render/from_snapshot.py``). The caller may still state it
    explicitly, which is what a future non-ICAAP freeze-minted family would do.
    """
    cycle = ((snapshot.get("metadata") or {}).get("icaap") or {}).get("cycle") or {}
    return cycle.get("kind") == "rehearsal"


def generate_frozen_package(  # noqa: PLR0913 - the mint key is its named parts
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    return_code: str,
    reporting_date: date,
    build: Callable[[], FrozenSnapshot],
    basis: str = "solo",
    notes: str | None = None,
    commit: bool = True,
    is_rehearsal: bool | None = None,
) -> RegulatoryPackage:
    """Mint an immutable package from a snapshot the FAMILY froze.

    The seam between a workspace freeze and the regulatory plane. The caller
    (the ICAAP freeze, and the ¶82 disclosure approval) owns everything about
    WHAT is in the snapshot; this owns everything about what a package IS —
    versioning, supersession, the content and register digests, the audit event
    — so an ICAAP filing is the same kind of object as every other return and is
    read, exported, signed and submitted by exactly the same code.

    Two deliberate differences from :func:`generate_package`:

    * **``effective_date`` is not gated.** ``registry.py`` records the rule:
      a commencement date is not a generation gate, because blocking on it would
      stop a bank preparing and dry-running a return before its first live
      filing. That mattered nowhere until ``ICAAP-REPORT`` became the first
      entry with an effective date (D-058), and the rehearsal path is the whole
      reason P3 can be proven at all. Every OTHER blocking dimension — the
      institution's licence class, its jurisdiction, its supervisor — still
      refuses, and the resolved state is recorded on the snapshot so a rehearsal
      filing says so about itself.
    * **``commit`` may be False**, so a caller that is minting this package as
      one step of a larger transaction (a freeze that also writes stage
      decisions and copies attachments) can roll the whole thing back. Nothing
      is half-frozen.
    """
    actor_user_id = require_actor(ctx)
    definition = REGISTRY.get(return_code)
    if definition is None:  # pragma: no cover - a caller naming an unregistered code
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Return code '{return_code}' is not registered.",
        )
    eligibility = resolve_eligibility(db, ctx, bank, as_of=reporting_date)
    eligibility.require(
        definition, reporting_date=reporting_date, ignore={"effective_date"}
    )
    effective_from = eligibility.effective_from(definition)
    built = build()
    # The withdrawn-evidence gate, HERE rather than in the caller (D-069, and
    # the architecture audit's M1). A freeze-minted filing binds engine runs
    # exactly as a generated one does — the ICAAP report binds every live block's
    # run plus every annex's ``source_runs`` — so the refusal belongs at the
    # mint, not in one family's freeze. Placed at the mint, the NEXT
    # freeze-minted family inherits it instead of having to remember it; placed
    # in ``freeze_cycle`` it would have to be re-remembered once per family.
    # Before anything is enriched, superseded or flushed, so a refusal leaves
    # nothing behind.
    _assert_evidence_current(db, ctx, built.source_runs)
    snapshot = built.snapshot
    _enrich_institution_block(db, ctx, bank, snapshot)
    _stamp_basis(snapshot, basis)
    _stamp_template_revision(definition, snapshot)
    # The authority record, for the same reason and from the same function the
    # generic mint site uses: "a new generator cannot ship without a stated
    # authority" was false for every freeze-minted family until this line
    # existed (architecture audit M3). ``declared_methodologies`` and the
    # per-section ``authority`` are the CF-1 disclosure — what an examiner reads
    # to learn which figure a return means.
    _stamp_provenance(
        db,
        ctx,
        bank,
        definition,
        reporting_date=reporting_date,
        snapshot=snapshot,
        source_runs=built.source_runs,
    )
    metadata = snapshot.setdefault("metadata", {})
    filing_meta = metadata.setdefault("filing", {})
    filing_meta["filing_role"] = definition.filing_role
    filing_meta["effective_from"] = effective_from.isoformat() if effective_from else None
    filing_meta["effective_from_parameter"] = definition.effective_from_parameter
    # A filing prepared before the return is in force is a REHEARSAL, and the
    # document says so rather than the reader having to infer it from dates.
    filing_meta["pre_effective"] = bool(
        effective_from is not None and reporting_date < effective_from
    )
    missing = eligibility.missing_effective_parameter(definition)
    if missing:
        filing_meta["effective_from_missing_parameter"] = missing
    rehearsal = _is_rehearsal(snapshot) if is_rehearsal is None else is_rehearsal
    filing_meta["is_rehearsal"] = rehearsal

    prior_current, prior_version, resubmission_authorization = _supersede_prior(
        db,
        ctx,
        bank,
        definition,
        reporting_date=reporting_date,
        basis=basis,
        rehearsal=rehearsal,
    )
    register_digest = (
        digests.register_state_digest(register_state.register_state_rows(db, ctx, bank.id))
        if not built.source_runs
        else None
    )
    package = RegulatoryPackage(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        return_family=definition.family,
        return_code=definition.code,
        reporting_date=reporting_date,
        frequency=definition.frequency,
        basis=basis,
        status="generated",
        version=(prior_version or 0) + 1,
        is_rehearsal=rehearsal,
        supersedes_id=prior_current.id if prior_current is not None else None,
        snapshot=snapshot,
        source_runs=built.source_runs,
        validation_report=None,
        generated_by=actor_user_id,
        generated_at=datetime.now(UTC),
        notes=notes,
        snapshot_sha256=snapshot_content_hash(snapshot),
        content_digest=digests.content_digest(snapshot),
        register_state_digest=register_digest,
    )
    db.add(package)
    db.flush()
    if resubmission_authorization is not None:
        resubmission_authorization.consumed_by_package_id = package.id
    record_event(
        db,
        ctx,
        event_type="regulatory_package.generated",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "bank_id": str(bank.id),
            "return_code": definition.code,
            "return_family": definition.family,
            "reporting_date": reporting_date.isoformat(),
            "basis": basis,
            "version": package.version,
            "supersedes_id": (str(prior_current.id) if prior_current is not None else None),
            "source_runs": [entry["run_id"] for entry in built.source_runs],
            "content_digest": package.content_digest,
            "register_state_digest": package.register_state_digest,
            "minted_by": "family_freeze",
            "pre_effective": filing_meta["pre_effective"],
            "is_rehearsal": rehearsal,
        },
    )
    logger.info(
        "regulatory_package.frozen return_code=%s family=%s bank=%s org=%s "
        "reporting_date=%s basis=%s version=%s pre_effective=%s",
        definition.code,
        definition.family,
        bank.id,
        ctx.organization_id,
        reporting_date.isoformat(),
        basis,
        package.version,
        filing_meta["pre_effective"],
    )
    if commit:
        db.commit()
    return package


def _row(code: str, description: str, value: Any, **extra: Any) -> dict[str, Any]:
    """One snapshot row.

    When the caller states a ``unit``, the normalised ``unit_kind`` rides along
    (P0-24 / NEW-8). Rows that state no unit get neither key, so the resolution
    rule stays simple and the snapshot stays lean:
    ``row.unit_kind ?? section.unit_kind``.
    """
    row = {"code": code, "description": description, "value": str(value), **extra}
    if "unit" in extra:
        row["unit_kind"] = unit_kind(extra["unit"])
    return row


#: Normalised measure kind. The snapshot carries TWO unit vocabularies for good
#: reason — they describe different axes:
#:
#: * generic rows say WHAT is measured (``"ghs"``, ``"pct"``, ``"years"`` …);
#: * ``bog_forms`` sheets say at WHAT SCALE the official sheet reports
#:   (``"millions"``, ``"thousands"``, ``"units"``, ``"percent"``, ``"count"``,
#:   ``"text"``) — the Guide's own unit convention, which must survive verbatim
#:   because it is printed on the official form.
#:
#: Collapsing one into the other would lose information a filed artifact needs.
#: So ``unit`` keeps each family's own vocabulary and ``unit_kind`` normalises
#: both onto one closed set a renderer can switch on. Every section and every
#: total carries both.
UNIT_KIND_CURRENCY = "currency"
UNIT_KIND_PERCENT = "percent"
UNIT_KIND_COUNT = "count"
UNIT_KIND_TEXT = "text"
UNIT_KIND_RATIO = "ratio"
UNIT_KIND_YEARS = "years"
UNIT_KIND_MIXED = "mixed"
UNIT_KIND_UNKNOWN = ""

_UNIT_KINDS: dict[str, str] = {
    # generic row vocabulary (measure)
    "ghs": UNIT_KIND_CURRENCY,
    "amount": UNIT_KIND_CURRENCY,
    "pct": UNIT_KIND_PERCENT,
    "ratio": UNIT_KIND_RATIO,
    "years": UNIT_KIND_YEARS,
    "count": UNIT_KIND_COUNT,
    "text": UNIT_KIND_TEXT,
    "mixed": UNIT_KIND_MIXED,
    # bog_forms sheet vocabulary (scale) — the first three are currency at a
    # stated scale, which ``unit`` still reports.
    "millions": UNIT_KIND_CURRENCY,
    "thousands": UNIT_KIND_CURRENCY,
    "units": UNIT_KIND_CURRENCY,
    "percent": UNIT_KIND_PERCENT,
}


def unit_kind(unit: str | None) -> str:
    """Normalise either unit vocabulary onto the closed ``unit_kind`` set.

    An unrecognised unit resolves to ``""`` — unknown, not "currency". Guessing
    currency for an unlabelled figure is how a percentage ends up rendered with
    a money symbol on a filed return.
    """
    return _UNIT_KINDS.get((unit or "").strip().lower(), UNIT_KIND_UNKNOWN)


def _infer_unit(rows: list[dict[str, Any]]) -> str:
    """The unit every row agrees on, ``"mixed"`` when they disagree, else ``""``.

    P0-24: the generic snapshot builders emitted no ``unit`` at all, so the UI's
    "units are shown per section" was true only for BSD forms (whose sheets each
    declare an official unit). Rows here already carry their own ``unit`` where
    the generator knows it, so the section's unit is a fact about the rows, not
    a new claim — and where the rows genuinely mix units (a table of amounts and
    ratios) the section says ``"mixed"`` rather than picking one and misleading
    the reader.
    """
    units = {str(row.get("unit", "")) for row in rows}
    units.discard("")
    if not units:
        return ""
    if len(units) == 1:
        return next(iter(units))
    return "mixed"


def _total(
    code: str,
    description: str,
    value: Any,
    *,
    equals_sum_of_rows: bool = False,
    unit: str = "",
) -> dict[str, Any]:
    """A section or headline total.

    ``unit`` (P0-24): a headline ratio previously lost its ``%`` on the way into
    the snapshot, because totals carried no unit for any renderer to read. The
    prior-period comparative section already looked for ``total["unit"]`` and
    always found nothing.
    """
    return {
        "code": code,
        "description": description,
        "value": str(value),
        "equals_sum_of_rows": equals_sum_of_rows,
        "unit": unit,
        "unit_kind": unit_kind(unit),
    }


def _section(  # noqa: PLR0913 — one keyword per snapshot-contract dimension
    code: str,
    title: str,
    rows: list[dict[str, Any]],
    total: dict[str, Any] | None = None,
    *,
    optional: bool = False,
    unit: str | None = None,
    authority: str | None = None,
) -> dict[str, Any]:
    """One snapshot section.

    ``unit`` (P0-24) is the section's unit of account. Passed explicitly where
    the generator knows it; otherwise inferred from the rows (see
    :func:`_infer_unit`). The ``bog_form`` builder passes the official sheet
    unit, so every section in every family now carries the key.

    ``authority`` (audit §10 item 3) is the :class:`ReportAuthority` that owns
    the section's figures. ``None`` here means "inherit the package authority";
    ``generate_package`` stamps it before the snapshot is sealed, so a stored
    snapshot never has a section without one.
    """
    resolved_unit = _infer_unit(rows) if unit is None else unit
    return {
        "code": code,
        "title": title,
        "optional": optional,
        "rows": rows,
        "total": total,
        "unit": resolved_unit,
        "unit_kind": unit_kind(resolved_unit),
        "authority": authority,
    }


def _summary_total(row: Bsd3SummaryRowRead, code: str, *, equals_sum: bool) -> dict[str, Any]:
    return _total(code, row.description, row.value, equals_sum_of_rows=equals_sum, unit=row.unit)


def _headline_comparative_section(totals: list[dict[str, Any]]) -> dict[str, Any]:
    """A T vs T−1 section carrying the headline totals plus a ``prior_value``
    slot the caller fills from the immediately-prior period (None until then).

    The BoG monthly returns show a Reporting-Month-vs-Previous-Month column
    (LMTD Table 1); this section is the snapshot's honest carrier for that
    convention. Values that have no prior stay blank — never fabricated.
    """
    rows = [
        {
            "code": total["code"],
            "description": total["description"],
            "value": total["value"],
            "unit": total.get("unit", ""),
            "unit_kind": total.get("unit_kind", unit_kind(total.get("unit"))),
            "prior_value": None,
        }
        for total in totals
    ]
    return _section(
        "headline_comparative",
        "Headline Totals — Reporting Period vs Previous Period",
        rows,
    )


def _stamp_template_revision(definition: ReturnDefinition, snapshot: dict[str, Any]) -> None:
    """Bind the snapshot to the template text it is generated under (D-021).

    Only templates whose wording has been revised carry a revision; an
    unstamped snapshot of one of them is a package generated before the
    revision and renders with the frozen earlier text, so its re-export never
    changes under it.
    """
    revision = TEMPLATE_REVISIONS.get(definition.template_id)
    if revision is not None:
        snapshot.setdefault("metadata", {})["template_revision"] = revision


def _stamp_basis(snapshot: dict[str, Any], basis: str) -> None:
    """Record the solo/consolidated reporting basis on the snapshot in place."""
    snapshot.setdefault("institution", {})["basis"] = basis
    snapshot.setdefault("metadata", {})["basis"] = basis


def _assert_evidence_current(
    db: Session, ctx: TenantContext, source_runs: list[dict[str, Any]]
) -> None:
    """Refuse to mint a package bound to a run whose inputs were withdrawn.

    Named rather than inlined because BOTH package-mint sites must run it and
    the seam between them is where gates have twice gone missing (D-069). An
    empty lineage is a pass — a master-data pack binds no engine evidence.
    """
    withdrawal_impact.assert_source_runs_current(
        db,
        _load_source_runs(db, ctx, source_runs),
        purpose="package_generation",
    )


def _stamp_provenance(  # noqa: PLR0913 — the full generation context is the input
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    definition: ReturnDefinition,
    *,
    reporting_date: date,
    snapshot: dict[str, Any],
    source_runs: list[dict[str, Any]],
) -> None:
    """Write the authority record onto the snapshot, in place, before sealing.

    Forensic audit §10 item 3: "Make report packages include explicit provenance
    for direct fact/template calculations: source period/fact generation,
    parameter versions, mapping version, template hash, formula evaluator
    version." This is that, for EVERY family, in one place — so a new generator
    cannot ship without a stated authority.

    Two cases:

    * The generator already declared one (``bog_form`` does, because only it
      knows its template digest, line-map digest and formula-cell count). Left
      untouched — that is the template-authoritative record.
    * Otherwise the authority is read off the source runs: a non-empty lineage
      means a sealed engine run owns the figures; an empty one on a generator
      that binds no run by design (the LRT corporate packs) means master data
      owns them, sealed by ``register_state_digest`` instead. This is the same
      branch the register-digest decision below already takes, now stated.

    Every section is then stamped with an authority, so no field in a stored
    snapshot is left without one. Resolution order for a consumer is
    ``row.authority ?? section.authority``.

    Takes the snapshot, its lineage and the reporting date rather than a
    ``GeneratedReturn`` + ``RegulatoryPackageCreate``, because the FROZEN mint
    site has neither and must run this too: a family that freezes its own
    snapshot is still a family, and "a new generator cannot ship without a
    stated authority" is only true if both mint sites can call this (D-069).
    """
    declared = snapshot.get("provenance")
    if isinstance(declared, dict) and declared.get("authority"):
        authority = str(declared["authority"])
    else:
        runs = _load_source_runs(db, ctx, source_runs)
        report_authority = (
            ReportAuthority.ENGINE_RUN if runs else ReportAuthority.MASTER_DATA_REGISTER
        )
        snapshot["provenance"] = build_engine_provenance(
            definition=definition,
            bank=bank,
            effective_date=reporting_date,
            runs=runs,
            authority=report_authority,
        ).to_dict()
        authority = report_authority.value
    for section in snapshot.get("sections") or []:
        if isinstance(section, dict) and not section.get("authority"):
            section["authority"] = authority


def _load_source_runs(
    db: Session, ctx: TenantContext, source_runs: list[dict[str, Any]]
) -> list[RegulatoryRun]:
    """Re-read the runs named in ``source_runs``, tenant-scoped, in that order.

    The generators hand back the wire-shaped entries; the full provenance view
    needs the rows. Reading them back rather than threading run objects through
    every generator keeps the change to the generators at zero, and a package is
    minted rarely enough that the extra reads are immaterial.
    """
    rows: list[RegulatoryRun] = []
    for entry in source_runs:
        raw = entry.get("run_id")
        if not raw:
            continue
        # ``source_runs`` carries the id as a STRING (it is JSON on the package
        # column); ``RegulatoryRun.id`` is a UUID column, and comparing the two
        # raises rather than silently missing.
        try:
            run_id = raw if isinstance(raw, UUID) else UUID(str(raw))
        except ValueError:  # pragma: no cover - a malformed lineage entry
            continue
        run = db.scalar(
            select(RegulatoryRun).where(
                RegulatoryRun.id == run_id,
                RegulatoryRun.organization_id == ctx.organization_id,
            )
        )
        if run is not None:
            rows.append(run)
    return rows


def _apply_prior_period_comparative(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    definition: ReturnDefinition,
    payload: RegulatoryPackageCreate,
    snapshot: dict[str, Any],
) -> None:
    """Fill the ``headline_comparative`` section's ``prior_value`` cells from the
    immediately-prior reporting period's current package for the same
    (bank, return_code, basis). No-op when the snapshot has no comparative
    section or no prior package exists — prior figures are never invented."""
    section = next(
        (
            item
            for item in snapshot.get("sections", [])
            if item.get("code") == "headline_comparative"
        ),
        None,
    )
    if section is None:
        return
    prior = db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.basis == payload.basis,
            RegulatoryPackage.reporting_date < payload.reporting_date,
            RegulatoryPackage.status != "superseded",
        )
        .order_by(
            RegulatoryPackage.reporting_date.desc(),
            RegulatoryPackage.version.desc(),
        )
        .limit(1)
    )
    if prior is None:
        return
    prior_totals = {row.get("code"): row.get("value") for row in prior.snapshot.get("totals", [])}
    for row in section.get("rows", []):
        row["prior_value"] = prior_totals.get(row.get("code"))
    snapshot.setdefault("metadata", {})["prior_period_reporting_date"] = (
        prior.reporting_date.isoformat()
    )


def _envelope(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
    sections: list[dict[str, Any]],
    totals: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "return_code": definition.code,
        "return_family": definition.family,
        "regulator": definition.regulator,
        "template_id": definition.template_id,
        "fidelity": definition.fidelity,
        "reporting_date": period.period_end.isoformat(),
        "institution": {
            "bank_id": str(bank.id),
            "name": bank.name,
            "short_name": bank.short_name,
            "currency": bank.currency,
            "jurisdiction_code": bank.jurisdiction_code,
            "license_type": bank.license_type,
        },
        "reporting_period": {
            "id": str(period.id),
            "label": period.label,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
        "sections": sections,
        "totals": totals,
        "metadata": {"generated_at": datetime.now(UTC).isoformat(), **metadata},
    }


def _enrich_institution_block(
    db: Session, ctx: TenantContext, bank: Bank, snapshot: dict[str, Any]
) -> None:
    """Fold corporate-profile master data (plan W4) into the envelope.

    When an ``InstitutionProfile`` row exists for the bank, the snapshot's
    institution block additionally carries its ORASS institution code (the
    identifier the regulator's portal knows the institution by). None-safe:
    no profile row means no key, and an unset code rides as ``null``.
    """
    profile = db.scalar(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank.id,
        )
    )
    if profile is None:
        return
    institution = snapshot.get("institution")
    if isinstance(institution, dict):
        institution["orass_institution_code"] = profile.orass_institution_code


def _source_run_entry(run: RegulatoryRun) -> dict[str, Any]:
    """The package's ``source_runs`` entry, built through WS-A's primitive.

    ``CalculationProvenance.source_run_entry()`` is byte-identical to the shape
    this function has always written ({module, run_id, input_hash,
    engine_version}), so adopting the formal provenance interface moves no
    snapshot hash and needs no migration. The RICH provenance — parameter
    digest, input/output schema versions, scenario, actor, computed_at — rides
    in the snapshot's ``provenance`` block instead of being duplicated here.

    Audit 2026-08-22 D-20: ``CalculationProvenance.require_complete()`` — WS-A's
    "may this run be filed from?" primitive — had zero production callers, so
    the package RECORDED ``provenance_complete``/``filable`` in its own
    authority block and bound the run regardless. Half the property was already
    enforced (only ``succeeded`` runs are selected); the other half is enforced
    here. Nothing is substituted for a missing element: a run that cannot say
    which period, which parameters or which engine produced it is not evidence,
    and a package built on it would carry a provenance chain with a hole in it.
    """
    _calculation_provenance(run).require_complete()
    return _calculation_source_run_entry(run)


def _latest_succeeded_runs_by_scenario(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod, module: str
) -> dict[str, RegulatoryRun]:
    runs = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    )
    latest: dict[str, RegulatoryRun] = {}
    for run in runs:
        latest.setdefault(run.scenario_code, run)
    return latest


def _baseline_run_or_409(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    *,
    artifact: str,
) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_baseline_run",
                "message": (
                    f"A successful baseline {module} run is required before {artifact} "
                    "can be generated for this reporting period. Run the engine first."
                ),
            },
        )
    return run


def _lcr_sections(preview: Any) -> list[dict[str, Any]]:
    """The LCR-tool sections shared by BSD3 and the LMT return (Table 11 subset)."""
    summary = {row.row_code: row for row in preview.summary_rows}
    return [
        _section(
            "hqla",
            "High Quality Liquid Assets",
            [_row(row.row_code, row.description, row.amount) for row in preview.hqla_rows],
            _summary_total(summary["3.0"], "hqla_total_ghs", equals_sum=True),
        ),
        _section(
            "outflows",
            "Cash Outflows (30 days)",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.outflow_rows
            ],
            _summary_total(summary["5.0"], "total_outflows_ghs", equals_sum=True),
        ),
        _section(
            "inflows",
            "Cash Inflows (30 days)",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.inflow_rows
            ],
            _summary_total(summary["7.0"], "capped_inflows_ghs", equals_sum=False),
        ),
        _section(
            "lcr_summary",
            "Liquidity Coverage Ratio Summary",
            [
                _row(row.row_code, row.description, row.value, unit=row.unit)
                for row in preview.summary_rows
            ],
        ),
    ]


def _lcr_totals(preview: Any) -> list[dict[str, Any]]:
    """Headline LCR totals shared by BSD3 and the LMT return."""
    summary = {row.row_code: row for row in preview.summary_rows}
    return [
        _row("hqla_total_ghs", summary["3.0"].description, summary["3.0"].value, unit="ghs"),
        _row("total_outflows_ghs", summary["5.0"].description, summary["5.0"].value, unit="ghs"),
        _row("net_outflows_30d_ghs", summary["8.0"].description, summary["8.0"].value, unit="ghs"),
        _row("lcr_pct", summary["9.0"].description, summary["9.0"].value, unit="pct"),
    ]


def _liquidity_metadata(preview: Any) -> dict[str, Any]:
    """Envelope metadata shared by BSD3 and the LMT return."""
    return {
        "form_code": preview.header.form_code,
        "form_title": preview.header.form_title,
        "regulator_name": preview.header.regulator,
        "preview_note": preview.header.preview_note,
        "baseline_run_id": str(preview.run_id),
        "engine_validations": [item.model_dump(mode="json") for item in preview.validations],
    }


def _generate_liquidity(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    preview = regulatory_liquidity.get_bsd3_preview(db, ctx, bank.id, period.id)
    sections = [
        *_lcr_sections(preview),
        _section(
            "nsfr_asf",
            "Available Stable Funding",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.nsfr.asf_rows
            ],
            _summary_total(preview.nsfr.asf_total, "asf_total_ghs", equals_sum=True),
        ),
        _section(
            "nsfr_rsf",
            "Required Stable Funding",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.nsfr.rsf_rows
            ],
            _summary_total(preview.nsfr.rsf_total, "rsf_total_ghs", equals_sum=True),
        ),
        _section(
            "nsfr_summary",
            "Net Stable Funding Ratio Summary",
            [
                _row(
                    preview.nsfr.nsfr_ratio.row_code,
                    preview.nsfr.nsfr_ratio.description,
                    preview.nsfr.nsfr_ratio.value,
                    unit=preview.nsfr.nsfr_ratio.unit,
                )
            ],
        ),
    ]
    totals = [
        *_lcr_totals(preview),
        _row(
            "asf_total_ghs",
            preview.nsfr.asf_total.description,
            preview.nsfr.asf_total.value,
            unit="ghs",
        ),
        _row(
            "rsf_total_ghs",
            preview.nsfr.rsf_total.description,
            preview.nsfr.rsf_total.value,
            unit="ghs",
        ),
        _row(
            "nsfr_pct",
            preview.nsfr.nsfr_ratio.description,
            preview.nsfr.nsfr_ratio.value,
            unit="pct",
        ),
    ]
    sections.append(_headline_comparative_section(totals))
    metadata = _liquidity_metadata(preview)
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(run) for _, run in sorted(runs.items())],
    )


def _generate_capital(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    preview = regulatory_capital.get_bsd2_preview(db, ctx, bank.id, period.id)
    cet1_rows = [
        _row(row.row_code, row.description, row.amount)
        for row in (*preview.cet1_rows, *preview.deduction_rows)
    ]
    sections = [
        _section(
            "cet1",
            "Common Equity Tier 1 (components and deductions)",
            cet1_rows,
            _total(
                "cet1_total_ghs",
                preview.cet1_total.description,
                preview.cet1_total.value,
                equals_sum_of_rows=True,
            ),
        ),
        _section(
            "at1",
            "Additional Tier 1 Capital",
            [_row(row.row_code, row.description, row.amount) for row in preview.at1_rows],
            optional=True,
        ),
        _section(
            "tier2",
            "Tier 2 Capital",
            [_row(row.row_code, row.description, row.amount) for row in preview.tier2_rows],
            optional=True,
        ),
        _section(
            "credit_rwa",
            "Credit Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.credit_rwa_rows
            ],
        ),
        _section(
            "market_rwa",
            "Market Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.market_rwa_rows
            ],
            optional=True,
        ),
        _section(
            "operational_rwa",
            "Operational Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.operational_rwa_rows
            ],
            optional=True,
        ),
        _section(
            "capital_ratios",
            "Capital Adequacy Ratios",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.value_pct,
                    unit="pct",
                    minimum_pct=str(row.minimum_pct),
                    passed=row.passed,
                )
                for row in preview.ratio_rows
            ],
        ),
    ]
    totals = [
        _row(
            "cet1_total_ghs", preview.cet1_total.description, preview.cet1_total.value, unit="ghs"
        ),
        _row(
            "tier1_total_ghs",
            preview.tier1_total.description,
            preview.tier1_total.value,
            unit="ghs",
        ),
        _row(
            "total_capital_ghs",
            preview.total_capital.description,
            preview.total_capital.value,
            unit="ghs",
        ),
        _row("total_rwa_ghs", preview.total_rwa.description, preview.total_rwa.value, unit="ghs"),
    ]
    sections.append(_headline_comparative_section(totals))
    metadata: dict[str, Any] = {
        "form_code": preview.header.form_code,
        "form_title": preview.header.form_title,
        "regulator_name": preview.header.regulator,
        "preview_note": preview.header.preview_note,
        "baseline_run_id": str(preview.run_id),
        "engine_validations": [item.model_dump(mode="json") for item in preview.validations],
    }
    baseline = db.get(RegulatoryRun, preview.run_id) if preview.run_id else None
    pending = [] if baseline is None else _pending_minimum_notes(baseline, period.period_end)
    if pending:
        metadata["report_notes"] = pending
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_CAPITAL)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(run) for _, run in sorted(runs.items())],
    )


# BoG GHS IRRBB calibration (plan W6.4): the IRRBB Guideline (exposure draft,
# Feb 2026) Appendix II–III Tables 5–6 prescribe a ±450 bp parallel GHS shock
# (short 500 / long 300). The IRR engine currently computes only the six Basel
# scenarios (app/domain/irr/engine.py IRR_SCENARIO_CODES) and hard-coded
# ±200 bp earnings-at-risk, so these rows are emitted ONLY when a run's
# metrics actually carry them — never fabricated. The BoG ±450 param rows
# (module='irr', parallel_up_450 / parallel_down_450) are stored effective-
# dated in param_stress_shock awaiting engine-side adoption (documented gap).
_BOG_GHS_450_EVE_ROWS: tuple[tuple[str, str], ...] = (
    ("eve_up_450_ghs", "ΔEVE under +450 bp parallel (BoG GHS calibration)"),
    ("eve_down_450_ghs", "ΔEVE under -450 bp parallel (BoG GHS calibration)"),
)
_BOG_GHS_450_EAR_ROWS: tuple[tuple[str, str], ...] = (
    ("ear_up_450_ghs", "Earnings at risk, +450 bp parallel (BoG GHS calibration)"),
    ("ear_down_450_ghs", "Earnings at risk, -450 bp parallel (BoG GHS calibration)"),
)


def _generate_irrbb(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _baseline_run_or_409(db, ctx, bank, period, MODULE_IRR, artifact="the IRRBB pilot return")
    metrics = run.metrics
    gap_rows = [
        _row(
            str(bucket["bucket"]),
            f"Repricing gap {bucket['bucket']}",
            bucket["gap_ghs"],
            rsa_ghs=str(bucket["rsa_ghs"]),
            rsl_ghs=str(bucket["rsl_ghs"]),
            cumulative_gap_ghs=str(bucket["cumulative_gap_ghs"]),
            within_12m=bool(bucket["within_12m"]),
        )
        for bucket in metrics.get("gap_buckets", [])
    ]
    eve_rows = [
        _row(
            str(scenario["scenario_code"]),
            f"ΔEVE under {scenario['scenario_code']}",
            scenario["delta_eve_ghs"],
            eve_ghs=str(scenario["eve_ghs"]),
            delta_eve_pct_tier1=str(scenario["delta_eve_pct_tier1"]),
            breach=bool(scenario["breach"]),
        )
        for scenario in metrics.get("eve_by_scenario", [])
    ]
    eve_rows.extend(
        _row(code, description, metrics[code])
        for code, description in _BOG_GHS_450_EVE_ROWS
        if code in metrics
    )
    ear_rows = [
        _row("ear_up_200_ghs", "Earnings at risk, +200 bp parallel", metrics["ear_up_200_ghs"]),
        _row("ear_down_200_ghs", "Earnings at risk, -200 bp parallel", metrics["ear_down_200_ghs"]),
        *(
            _row(code, description, metrics[code])
            for code, description in _BOG_GHS_450_EAR_ROWS
            if code in metrics
        ),
        _row("nii_base_ghs", "Base net interest income", metrics["nii_base_ghs"]),
    ]
    summary_rows = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row(
            "worst_eve_change_ghs",
            f"Worst-case EVE change ({metrics['worst_scenario']})",
            metrics["worst_eve_change_ghs"],
        ),
        _row(
            "worst_eve_change_pct_tier1",
            "Worst-case EVE change as % of Tier 1",
            metrics["worst_eve_change_pct_tier1"],
        ),
        _row("duration_gap", "Duration gap (years)", metrics["duration_gap"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    sections = [
        _section("repricing_gap", "Repricing Gap by Bucket", gap_rows),
        _section("eve_scenarios", "ΔEVE by Supervisory Shock", eve_rows),
        _section("earnings_at_risk", "ΔNII / Earnings at Risk", ear_rows),
        _section("summary", "IRRBB Summary", summary_rows),
    ]
    totals = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row("worst_eve_change_ghs", "Worst-case EVE change", metrics["worst_eve_change_ghs"]),
        _row(
            "worst_eve_change_pct_tier1",
            "Worst-case EVE change as % of Tier 1",
            metrics["worst_eve_change_pct_tier1"],
        ),
        _row(
            "cumulative_12m_gap_ghs",
            "Cumulative 12-month repricing gap",
            metrics["cumulative_12m_gap_ghs"],
        ),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    bog_450_keys = {code for code, _ in (*_BOG_GHS_450_EVE_ROWS, *_BOG_GHS_450_EAR_ROWS)}
    metadata = {
        "worst_scenario": metrics.get("worst_scenario"),
        "eve_limit_pct": metrics.get("eve_limit_pct"),
        "baseline_run_id": str(run.id),
        # True only when the engine actually computed the BoG ±450 bp GHS
        # shock set; while False, the ±450 rows are honestly absent (engine
        # gap — see the IRRBB template notes).
        "bog_ghs_450_rows_present": any(code in metrics for code in bog_450_keys),
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_IRR)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(item) for _, item in sorted(runs.items())],
    )


def _generate_sdi_irrbb(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """SDI IRRBB pilot packet without a bank Tier 1 outlier assertion."""
    run = _baseline_run_or_409(db, ctx, bank, period, MODULE_IRR, artifact="the SDI IRRBB return")
    metrics = run.metrics
    gap_rows = [
        _row(
            str(bucket["bucket"]),
            f"Repricing gap {bucket['bucket']}",
            bucket["gap_ghs"],
            rsa_ghs=str(bucket["rsa_ghs"]),
            rsl_ghs=str(bucket["rsl_ghs"]),
            cumulative_gap_ghs=str(bucket["cumulative_gap_ghs"]),
            within_12m=bool(bucket["within_12m"]),
        )
        for bucket in metrics.get("gap_buckets", [])
    ]
    eve_rows = [
        _row(
            str(scenario["scenario_code"]),
            f"Delta EVE under {scenario['scenario_code']}",
            scenario["delta_eve_ghs"],
            eve_ghs=str(scenario["eve_ghs"]),
        )
        for scenario in metrics.get("eve_by_scenario", [])
    ]
    ear_rows = [
        _row("ear_up_200_ghs", "Earnings at risk, +200 bp parallel", metrics["ear_up_200_ghs"]),
        _row("ear_down_200_ghs", "Earnings at risk, -200 bp parallel", metrics["ear_down_200_ghs"]),
        *(
            _row(code, description, metrics[code])
            for code, description in _BOG_GHS_450_EAR_ROWS
            if code in metrics
        ),
        _row("nii_base_ghs", "Base net interest income", metrics["nii_base_ghs"]),
    ]
    summary_rows = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row(
            "worst_eve_change_ghs",
            f"Worst-case EVE change ({metrics['worst_scenario']})",
            metrics["worst_eve_change_ghs"],
        ),
        _row("duration_gap", "Duration gap (years)", metrics["duration_gap"]),
    ]
    sections = [
        _section("repricing_gap", "Repricing Gap by Bucket", gap_rows),
        _section("eve_scenarios", "Appendix IV — Delta EVE by Actual Shock", eve_rows),
        _section("earnings_at_risk", "Delta NII / Earnings at Risk", ear_rows),
        _section("summary", "SDI IRRBB Summary", summary_rows),
    ]
    totals = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row("worst_eve_change_ghs", "Worst-case EVE change", metrics["worst_eve_change_ghs"]),
        _row(
            "cumulative_12m_gap_ghs",
            "Cumulative 12-month repricing gap",
            metrics["cumulative_12m_gap_ghs"],
        ),
    ]
    bog_450_keys = {code for code, _ in (*_BOG_GHS_450_EVE_ROWS, *_BOG_GHS_450_EAR_ROWS)}
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_IRR)
    return GeneratedReturn(
        snapshot=_envelope(
            bank,
            period,
            definition,
            sections,
            totals,
            {
                "worst_scenario": metrics.get("worst_scenario"),
                "baseline_run_id": str(run.id),
                "capital_denominator": "Net Own Funds (Act 930 s.29)",
                "tier1_outlier_verdict": "not_assessed_for_sdi",
                "bog_ghs_450_rows_present": any(code in metrics for code in bog_450_keys),
            },
        ),
        source_runs=[_source_run_entry(item) for _, item in sorted(runs.items())],
    )


def _generate_fx(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_FX, artifact="the Net Open Position return"
    )
    metrics = run.metrics
    position_rows = [
        _row(
            str(currency["currency"]),
            f"Net open position in {currency['currency']}",
            currency["net_ghs"],
            side=str(currency["side"]),
            net_ccy=str(currency["net_ccy"]),
            spot_ghs=str(currency["spot_ghs"]),
            abs_pct_tier1=str(currency["abs_pct_tier1"]),
            within_single_limit=bool(currency["within_single_limit"]),
        )
        for currency in metrics.get("currencies", [])
    ]
    var_rows = [
        _row(
            str(item["currency"]),
            f"Standalone VaR for {item['currency']}",
            item["standalone_var_ghs"],
            net_ghs=str(item["net_ghs"]),
        )
        for item in metrics.get("standalone_vars", [])
    ]
    hedge_rows = [
        _row(
            str(hedge["hedge_id"]),
            f"{hedge['instrument']} hedge on {hedge['pair']}",
            hedge["mtm_ghs"],
            prospective_r2_pct=str(hedge["prospective_r2_pct"]),
            dollar_offset_pct=str(hedge["dollar_offset_pct"]),
            effective=bool(hedge["effective"]),
        )
        for hedge in metrics.get("hedges", [])
    ]
    scenario_rows = [
        _row(
            str(scenario["scenario_code"]),
            f"NOP under {scenario['scenario_code']}",
            scenario["nop_ghs"],
            shock_pct=str(scenario["shock_pct"]),
            nop_pct_tier1=str(scenario["nop_pct_tier1"]),
            within_aggregate_limit=bool(scenario["within_aggregate_limit"]),
        )
        for scenario in metrics.get("nop_by_scenario", [])
    ]
    summary_rows = [
        _row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"]),
        _row("nop_pct_tier1", "Aggregate NOP as % of Tier 1", metrics["nop_pct_tier1"]),
        _row("sum_long_ghs", "Sum of long positions", metrics["sum_long_ghs"]),
        _row("sum_short_ghs", "Sum of short positions", metrics["sum_short_ghs"]),
        _row("var_99_1d_ghs", "Portfolio VaR (99%, 1-day)", metrics["var_99_1d_ghs"]),
        _row("stressed_var_ghs", "Stressed VaR (cedi crisis)", metrics["stressed_var_ghs"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    sections = [
        _section("currency_positions", "Net Open Position by Currency", position_rows),
        _section("standalone_var", "Standalone Value at Risk by Currency", var_rows),
        _section("hedges", "Hedge Effectiveness", hedge_rows, optional=True),
        _section("scenario_nop", "NOP under Depreciation Scenarios", scenario_rows),
        _section("nop_summary", "Net Open Position Summary", summary_rows),
    ]
    totals = [
        _row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"]),
        _row("nop_pct_tier1", "Aggregate NOP as % of Tier 1", metrics["nop_pct_tier1"]),
        _row("var_99_1d_ghs", "Portfolio VaR (99%, 1-day)", metrics["var_99_1d_ghs"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    metadata = {
        "single_ccy_max_currency": metrics.get("single_ccy_max_currency"),
        "nop_single_limit_pct": metrics.get("nop_single_limit_pct"),
        "nop_aggregate_limit_pct": metrics.get("nop_aggregate_limit_pct"),
        "baseline_run_id": str(run.id),
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_FX)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(item) for _, item in sorted(runs.items())],
    )


def _generate_icaap_stress(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    forecast_run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == MODULE_FORECAST,
            RegulatoryRun.status == "succeeded",
            regulatory_forecasting.regulatory_horizon_clause(),
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if forecast_run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_forecast_run",
                "message": (
                    "A successful 5-year forecast run is required before the ICAAP data "
                    "companion can be generated for this reporting period. Run the "
                    "forecast module first."
                ),
            },
        )
    metrics = forecast_run.metrics
    summary_rows = [
        _row(code, f"Forecast summary: {code}", metrics[code])
        for code in _FORECAST_SUMMARY_FIELDS
        if code in metrics
    ]
    path_rows = [
        _row(
            f"year_{entry['year']}",
            f"Projected position for {entry['period_label']}",
            entry["total_assets"],
            **{key: value for key, value in entry.items() if key not in ("year", "period_label")},
        )
        for entry in metrics.get("path", [])
    ]

    stress_runs: list[RegulatoryRun] = []
    stress_rows: list[dict[str, Any]] = []
    for module, headline in ((MODULE_LIQUIDITY, "lcr_pct"), (MODULE_CAPITAL, "car_pct")):
        latest = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, module)
        for scenario_code, run in sorted(latest.items()):
            if scenario_code == BASELINE_SCENARIO:
                continue
            stress_runs.append(run)
            stress_rows.append(
                _row(
                    f"{module}:{scenario_code}",
                    f"{module} stress scenario '{scenario_code}' ({headline})",
                    run.metrics.get(headline, "0"),
                    module=module,
                    scenario_code=scenario_code,
                    input_hash=run.input_hash,
                )
            )

    sections = [
        _section("forecast_summary", "5-Year Forecast Summary", summary_rows),
        _section("forecast_path", "Projected Balance-Sheet Path", path_rows),
        _section("stress_summary", "Stress Scenario Outcomes", stress_rows, optional=True),
    ]
    # Reverse stress (Stress Testing Guideline ¶36): the latest stored reverse-
    # stress run for the period, re-tabulated through the SAME row builder the
    # Board/ALCO stress pack uses. Absent a run the section is omitted and the
    # package says so — no frontier is ever inferred.
    reverse_run = _latest_reverse_stress_run(db, ctx, bank, period)
    frontier_rows = _stress_frontier_rows(reverse_run) if reverse_run is not None else []
    if reverse_run is not None:
        sections.append(
            _section(
                "reverse_stress",
                "Reverse Stress Test Summary",
                frontier_rows,
                optional=True,
            )
        )
    totals = [
        _row(
            code,
            f"Forecast summary: {code}",
            metrics[code],
        )
        for code in ("cumulative_net_income", "min_car_pct", "min_lcr_pct", "min_nsfr_pct")
        if code in metrics
    ]
    metadata: dict[str, Any] = {
        "forecast_run_id": str(forecast_run.id),
        "forecast_scenario_code": forecast_run.scenario_code,
        "assumptions": metrics.get("assumptions", {}),
        "stress_run_count": len(stress_runs),
    }
    source_runs = [_source_run_entry(forecast_run)] + [
        _source_run_entry(run) for run in stress_runs
    ]
    if reverse_run is not None:
        metadata["reverse_stress_run_id"] = str(reverse_run.id)
        metadata["reverse_stress_narrative"] = _reverse_stress_narrative(reverse_run)
        metadata["report_notes"] = [_reverse_stress_note(frontier_rows)]
        source_runs.append(_source_run_entry(reverse_run))
    else:
        absent = (
            "No reverse stress test has been run for this reporting period, so the "
            "reverse stress test summary is omitted; nothing is substituted for it."
        )
        metadata["report_notes"] = [absent]
        metadata["generation_findings"] = [
            {"rule": "icaap_reverse_stress", "severity": "INFO", "detail": absent}
        ]
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=source_runs,
    )


# --- ICAAP Appendix II submission (docs/stress.md §1.8, §3.4, §3.8, Phase 5) ---
# The directive's regulatory deliverable: a return whose snapshot IS the BoG
# Stress Testing Guideline Appendix II Tables 1–6 (¶67, ¶68), sourced from a
# Board-ATTESTED enterprise-stress RegulatoryRun. The enterprise-stress engine
# already produced the tables (``metrics.appendix_ii``); this generator only
# re-tabulates them into the regulatory-package-v1 envelope so the return
# inherits maker-checker, package immutability, the content digest, the default
# signing policy and PDF/xlsx export unchanged. It never recomputes a figure —
# every value is carried verbatim from the immutable run (values already in the
# directive's GHS'000 reporting unit, so the rendered columns do NOT re-scale).

_APPENDIX2_LABELS: dict[str, str] = {
    "current": "Current (as-of)",
}


def _appendix2_period_label(label: str) -> str:
    """Human description for an Appendix II snapshot label (current / base / stress)."""
    if label in _APPENDIX2_LABELS:
        return _APPENDIX2_LABELS[label]
    for prefix, wording in (
        ("base_y", "Base Case — Year "),
        ("stress_y", "Stress (Adverse) — Year "),
        ("post_cap_y", "Post-capitalisation — Year "),
    ):
        if label.startswith(prefix):
            return f"{wording}{label.removeprefix(prefix)}"
    return label


def _appendix2_row(code: str, description: str, fields: dict[str, Any]) -> dict[str, Any]:
    """A snapshot row that preserves ``None`` slots (a directive line with no
    honest source stays blank, never a fabricated zero) — unlike ``_row``, which
    stringifies its value. Values are carried verbatim from the serialized
    Appendix II tables (already strings or ``None``)."""
    return {"code": code, "description": description, **fields}


def _appendix2_snapshot_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    """One capital-position row (Table 1 summary / post-capitalisation)."""
    return _appendix2_row(
        snapshot["label"],
        _appendix2_period_label(snapshot["label"]),
        {
            "value": snapshot.get("total_regulatory_capital"),
            "cet1": snapshot.get("cet1"),
            "tier1": snapshot.get("tier1"),
            "tier2": snapshot.get("tier2"),
            "total_rwa": snapshot.get("total_rwa"),
            "cet1_ratio_pct": snapshot.get("cet1_ratio_pct"),
            "tier1_ratio_pct": snapshot.get("tier1_ratio_pct"),
            "car_pct": snapshot.get("car_pct"),
            "paid_up": snapshot.get("paid_up"),
        },
    )


def _appendix2_table1_sections(  # noqa: PLR0912, PLR0915 - one flat table mapping
    table1: dict[str, Any],
    *,
    basel_applicable: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Table 1 (Summary Results) → its snapshot sections + headline totals."""
    positions = [_appendix2_snapshot_row(table1["current"])]
    positions += [_appendix2_snapshot_row(row) for row in table1["pre_adverse"]]
    positions += [_appendix2_snapshot_row(row) for row in table1["post_adverse"]]

    impact_rows: list[dict[str, Any]] = []
    for entry in table1["impact_of_adverse"]:
        year = entry["year"]
        for loss in entry["losses"]:
            impact_rows.append(
                _appendix2_row(
                    f"y{year}:{loss['exposure_class']}",
                    appendix2_exposure_class_label(loss["exposure_class"]),
                    {"value": loss["loss"], "year": str(year)},
                )
            )

    required_by_year: dict[int, dict[str, Any]] = {}
    for entry in table1["capital_required_car_target"]:
        required_by_year.setdefault(entry["year"], {})["car"] = entry["amount"]
    for entry in table1["capital_required_paid_up"]:
        required_by_year.setdefault(entry["year"], {})["paid_up"] = entry["amount"]
    required_rows = [
        _appendix2_row(
            f"y{year}",
            f"Stress (Adverse) — Year {year}",
            {"value": amounts.get("car"), "paid_up_shortfall": amounts.get("paid_up")},
        )
        for year, amounts in sorted(required_by_year.items())
    ]

    sections = [
        _section(
            "t1_summary_positions",
            "Appendix II Table 1 — Summary Results (Capital Positions)",
            positions,
        ),
        _section(
            "t1_impact_of_adverse",
            "Appendix II Table 1 — Impact of Adverse "
            f"(Loss by {'CRD ' if basel_applicable else ''}Exposure Class)",
            impact_rows,
        ),
        _section(
            "t1_capital_required",
            "Appendix II Table 1 — Capital Required to Meet Minima",
            required_rows,
        ),
    ]

    # With / without management actions (¶67(f)). Present only when the run
    # modelled an approved management-actions plan; otherwise the pre-action
    # projection stands alone and these optional sections are omitted.
    management = table1.get("management_actions")
    if management is not None:
        action_rows = [
            _appendix2_row(
                f"y{row['year']}",
                f"Stress (Adverse) — Year {row['year']}",
                {
                    "value": row.get("total_management_actions"),
                    "capital_raised_total": row.get("capital_raised_total"),
                    "revision_of_dividend_policy": row.get("revision_of_dividend_policy"),
                    "change_in_business_strategy": row.get("change_in_business_strategy"),
                    "sale_of_assets": row.get("sale_of_assets"),
                    "risk_reduction": row.get("risk_reduction"),
                    "other": row.get("other"),
                    "rwa_relief_total": row.get("rwa_relief_total"),
                },
            )
            for row in management["rows"]
        ]
        sections.append(
            _section(
                "t1_management_actions",
                "Appendix II Table 1 — Management Actions (with-actions)",
                action_rows,
                optional=True,
            )
        )
    post_cap = table1.get("post_capitalisation")
    if post_cap is not None:
        sections.append(
            _section(
                "t1_post_capitalisation",
                "Appendix II Table 1 — Post-capitalisation (Stress Case with Actions)",
                [_appendix2_snapshot_row(row) for row in post_cap],
                optional=True,
            )
        )
    residual = table1.get("residual_capital_required_after_actions")
    if residual is not None:
        residual_rows = [
            _appendix2_row(
                f"y{row['year']}",
                f"Stress (Adverse) — Year {row['year']}",
                {"value": row.get("residual_capital_required")},
            )
            for row in residual["rows"]
        ]
        sections.append(
            _section(
                "t1_residual",
                "Appendix II Table 1 — Residual Capital Required After Actions",
                residual_rows,
                optional=True,
            )
        )

    last_stress = table1["post_adverse"][-1] if table1["post_adverse"] else {}
    totals = [
        _row(
            "capital_gap",
            "Capital gap (worst year, pre-management-action)",
            table1["capital_gap"],
        ),
        _row(
            "stressed_car_end_pct",
            "Stressed CAR at end of horizon",
            last_stress.get("car_pct", "0"),
        ),
    ]
    return sections, totals


def _appendix2_table2_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    built = [
        _appendix2_row(
            row["label"],
            _appendix2_period_label(row["label"]),
            {
                "value": row.get("total_regulatory_capital"),
                "gross_cet1": row["cet1"].get("gross_cet1"),
                "total_deductions": row["cet1"].get("total_deductions"),
                "cet1_after_deductions": row["cet1"].get("cet1_after_deductions"),
                "at1_eligible": row.get("at1_eligible"),
                "tier2_eligible": row.get("tier2_eligible"),
                "credit_risk_reserve": row.get("credit_risk_reserve"),
                "total_rwa": row.get("total_rwa"),
            },
        )
        for row in rows
    ]
    return _section(
        "t2_capital_projection",
        "Appendix II Table 2 — Regulatory Capital Projection",
        built,
    )


def _appendix2_table3_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    built = [
        _appendix2_row(
            row["label"],
            _appendix2_period_label(row["label"]),
            {
                "value": row.get("profit_after_tax"),
                "net_interest_income": row.get("net_interest_income"),
                "fees_and_commissions": row.get("fees_and_commissions"),
                "operating_expenses": row.get("operating_expenses"),
                "impairment_losses": row.get("impairment_losses"),
                "profit_before_tax": row.get("profit_before_tax"),
                "tax": row.get("tax"),
                "distributions": row.get("distributions"),
                "adjusted_retained_earnings_for_car": row.get("adjusted_retained_earnings_for_car"),
            },
        )
        for row in rows
    ]
    return _section(
        "t3_profit_and_loss",
        "Appendix II Table 3 — Movement in Profit & Loss",
        built,
    )


def _appendix2_table4_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _sum_deposits(row: dict[str, Any]) -> str | None:
        parts = [
            row.get("demand_deposits"),
            row.get("savings_deposits"),
            row.get("time_deposits"),
            row.get("other_deposits"),
        ]
        if all(part is None for part in parts):
            return None
        return str(sum((Decimal(str(part)) for part in parts if part is not None), Decimal("0")))

    built = [
        _appendix2_row(
            row["label"],
            _appendix2_period_label(row["label"]),
            {
                "value": row.get("total_assets"),
                "loans": row.get("loans"),
                "cash_and_balances": row.get("cash_and_balances"),
                "short_term_investments": row.get("short_term_investments"),
                "other_assets": row.get("other_assets"),
                "total_liabilities": row.get("total_liabilities"),
                "total_deposits": _sum_deposits(row),
                "borrowings": row.get("borrowings"),
                "capital": row.get("capital"),
            },
        )
        for row in rows
    ]
    return _section(
        "t4_financial_position",
        "Appendix II Table 4 — Statement of Financial Position",
        built,
    )


def _is_nil(value: Any) -> bool:
    try:
        return value is not None and Decimal(str(value)) == 0
    except InvalidOperation:
        return False


def _appendix2_pillar2(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """One Table 5 column's Pillar 2 block, and the risks it has NOT modelled.

    ``None`` means no Pillar 2 amount is recorded for the risk ("Not modelled");
    ``0`` means the risk WAS assessed and the charge is nil, and prints as 0.00.
    The two are never folded together (regulatory audit P0R-6). The run's
    ``total`` is the sum of the recorded risks, so a column with nothing recorded
    carried ``0.000``, which a filed Table 5 prints as a zero requirement: a
    total over nothing is itself not modelled (``None``); a total over some but
    not all risks is kept and declared partial through ``pillar2_coverage``.
    """
    pillar2 = row.get("pillar2") or {}
    values = {field: pillar2.get(key) for field, key, _ in APPENDIX2_PILLAR2_RISKS}
    missing = [label for field, _, label in APPENDIX2_PILLAR2_RISKS if values[field] is None]
    nil = [label for field, _, label in APPENDIX2_PILLAR2_RISKS if _is_nil(values[field])]
    if len(missing) == len(APPENDIX2_PILLAR2_RISKS):
        total = None
        coverage = NOT_MODELLED
    else:
        total = pillar2.get("total")
        coverage = (
            "Partial — excludes " + ", ".join(missing) if missing else "All Pillar 2 risks assessed"
        )
        if nil:
            coverage += "; assessed nil: " + ", ".join(nil)
    return {**values, "pillar2_total": total, "pillar2_coverage": coverage}, missing


def _appendix2_table5_sections(
    table5: dict[str, Any], *, include_pillar2: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Table 5 → the RWA/requirements grid, the per-risk Pillar 2 grid, and the
    advisory findings about Pillar 2 coverage (INFO/WARNING only).

    ``include_pillar2=False`` is the SDI packet (D-020): SDIs are outside the
    ICAAP Guideline and have no Pillar 2 regime, so neither the Pillar 2 grid,
    nor a Pillar 2 total, nor a Pillar 2 coverage finding is produced — and the
    row carries the requirement at the governed CAR only, never a "total" that
    could hold an engine-side add-on under a name the regime does not have.
    """
    rwa_rows: list[dict[str, Any]] = []
    pillar2_rows: list[dict[str, Any]] = []
    unmodelled_columns: list[str] = []
    for row in table5["rows"]:
        label = row["label"]
        description = _appendix2_period_label(label)
        fields: dict[str, Any] = {
            "value": row.get("total_pillar1_rwa"),
            "credit_rwa": row.get("credit_rwa"),
            "operational_rwa": row.get("operational_rwa"),
            "market_rwa": row.get("market_rwa"),
            "pillar1_requirement": row.get("pillar1_requirement"),
        }
        if include_pillar2:
            pillar2, missing = _appendix2_pillar2(row)
            if len(missing) == len(APPENDIX2_PILLAR2_RISKS):
                unmodelled_columns.append(description)
            fields["pillar2_total"] = pillar2["pillar2_total"]
            fields["total_capital_requirement"] = row.get("total_capital_requirement")
            pillar2_rows.append(_appendix2_row(label, description, pillar2))
        rwa_rows.append(_appendix2_row(label, description, fields))
    findings: list[dict[str, str]] = []
    if unmodelled_columns:
        findings.append(
            {
                "rule": "appendix2_pillar2_coverage",
                "severity": "WARNING",
                "detail": (
                    "Appendix II Table 5 records no Pillar 2 capital requirement for: "
                    + "; ".join(unmodelled_columns)
                    + f". Those columns print '{NOT_MODELLED}', and their Total Capital "
                    "Requirements figure is the Pillar 1 requirement alone, which "
                    "understates the requirement until the Pillar 2 assessment for them "
                    "is recorded."
                ),
            }
        )
    sections = [
        _section(
            "t5_rwa",
            "Appendix II Table 5 — Evolution of RWA & Capital Requirements"
            if include_pillar2
            else "Evolution of RWA & Capital Requirement",
            rwa_rows,
        )
    ]
    if include_pillar2:
        sections.append(
            _section(
                "t5_pillar2",
                "Appendix II Table 5 — Pillar 2 Capital Requirements by Risk",
                pillar2_rows,
                optional=True,
            )
        )
    return sections, findings


#: Table 6 drivers the directive asks for as a year-on-year CHANGE while the
#: scenario authors a LEVEL (Appendix III Table 6: "Year-on-Year Changes in Stock
#: Market Valuation (GSE Index)"). Shown as the change, never as the level.
_APPENDIX2_YOY_DRIVERS = frozenset({"gse_index"})
_YOY_QUANTUM = Decimal("0.000001")


def _year_on_year(level: Any, prior: Any) -> str | None:
    """The fractional change from ``prior`` to ``level``, or ``None`` when either
    is absent or the prior level is zero (no change can be stated)."""
    try:
        current = Decimal(str(level)) if level is not None else None
        previous = Decimal(str(prior)) if prior is not None else None
    except InvalidOperation:
        return None
    if current is None or previous is None or previous == 0:
        return None
    return str((current / previous - 1).quantize(_YOY_QUANTUM))


def _appendix2_table6_section(table6: dict[str, Any]) -> dict[str, Any]:
    by_variable: dict[str, dict[int, dict[str, Any]]] = {}
    for row in table6["rows"]:
        by_variable.setdefault(row["variable"], {})[int(row["year_index"])] = row
    built: list[dict[str, Any]] = []
    for row in table6["rows"]:
        variable = row["variable"]
        base_value = row.get("base_value")
        stress_value = row.get("stress_value")
        if variable in _APPENDIX2_YOY_DRIVERS:
            prior = by_variable[variable].get(int(row["year_index"]) - 1) or {}
            base_value = _year_on_year(base_value, prior.get("base_value"))
            stress_value = _year_on_year(stress_value, prior.get("stress_value"))
        built.append(
            _appendix2_row(
                f"{variable}:y{row['year_index']}",
                appendix2_risk_driver_label(variable),
                {
                    "value": stress_value,
                    "year_index": str(row["year_index"]),
                    "base_value": base_value,
                    "stress_value": stress_value,
                },
            )
        )
    return _section(
        "t6_risk_drivers",
        "Appendix II Table 6 — Key Risk Drivers & Forecasting Assumptions",
        built,
    )


def _generate_attested_appendix2(  # noqa: PLR0913 — five are the standard
    # generator signature (db, ctx, bank, period, definition); the sixth is the
    # keyword-only flag that separates the SDI packet from the bank one.
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
    *,
    include_basel_table2: bool,
) -> GeneratedReturn:
    """Build an Appendix II-derived stress submission from attested evidence.

    Sourced ONLY from a Board-ATTESTED enterprise-stress run: the sign-off gate
    (docs/stress.md §3.8) is what designates which immutable run is the annual
    submission, so a bank cannot file stress results the Board has not reviewed
    and challenged (¶20). Refuses (409 ``no_attested_stress_run``) when no such
    run exists.
    """
    from app.services import enterprise_stress_signoff  # noqa: PLC0415 - avoids import cycle

    run = enterprise_stress_signoff.resolve_attested_run_for_period(db, ctx, bank.id, period.id)
    signoff = enterprise_stress_signoff.latest_signoff_for_run(db, ctx, run.id)
    appendix = (run.metrics or {}).get("appendix_ii")
    if not isinstance(appendix, dict) or "table1_summary" not in appendix:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "appendix_ii_missing",
                "message": (
                    "The attested enterprise-stress run carries no Appendix II tables; "
                    "re-run the enterprise stress test before submitting."
                ),
            },
        )

    table1 = appendix["table1_summary"]
    t1_sections, t1_totals = _appendix2_table1_sections(
        table1, basel_applicable=include_basel_table2
    )
    t5_sections, findings = _appendix2_table5_sections(
        appendix["table5_rwa"], include_pillar2=include_basel_table2
    )
    sections = [
        *t1_sections,
        _appendix2_table3_section(appendix["table3_profit_and_loss"]),
        _appendix2_table4_section(appendix["table4_financial_position"]),
        *t5_sections,
        _appendix2_table6_section(appendix["table6_risk_drivers"]),
        _appendix2_governance_section(run, signoff),
    ]
    if include_basel_table2:
        sections.insert(len(t1_sections), _appendix2_table2_section(appendix["table2_capital"]))
    if signoff is not None:
        narrative, narrative_findings = _appendix2_narrative_section(db, ctx, signoff)
        sections.append(narrative)
        findings.extend(narrative_findings)

    provenance = _appendix2_parameter_provenance(
        run,
        table1,
        period,
        appendix.get("unit") or "",
        basel_applicable=include_basel_table2,
    )
    findings.extend(_weaker_minimum_findings(provenance))
    metadata: dict[str, Any] = {
        # The reporting unit comes from the stress run that produced these
        # tables. It used to default to ``GHS'000`` — a currency literal on a
        # filed artifact, which is the exact leak the jurisdiction-neutrality
        # rule exists to prevent (CLAUDE.md: jurisdiction is data; a Nigerian
        # bank must never be labelled in cedis). When the run does not state a
        # unit the metadata says so, and the renderer falls back to the
        # template's declared currency unit rather than to Ghana.
        "unit": appendix.get("unit") or "",
        "scenario_code": run.scenario_code,
        "horizon_years": appendix.get("horizon_years"),
        "car_target_pct": table1.get("car_target_pct"),
        "paid_up_min": table1.get("paid_up_min"),
        "with_management_actions": table1.get("management_actions") is not None,
        "enterprise_stress_run_id": str(run.id),
        "enterprise_stress_input_hash": run.input_hash,
        "basel_table2_included": include_basel_table2,
        # The minima the capital-requirement lines were measured against, with
        # the governed row that supplied each — the report notes state them, so
        # the column headers can stay free of a hard-coded percentage.
        "parameter_provenance": provenance,
    }
    if findings:
        metadata["generation_findings"] = findings
    if signoff is not None:
        metadata["governance"] = {
            "signoff_id": str(signoff.id),
            "status": signoff.status,
            "scenario_narrative": signoff.scenario_narrative,
            "assumptions_rationale": signoff.assumptions_rationale,
            "methodology_summary": signoff.methodology_summary,
            "board_challenge": signoff.board_challenge,
            "credibility_rationale": signoff.credibility_rationale,
            "attested_by": str(signoff.attested_by) if signoff.attested_by else None,
            "attested_at": signoff.attested_at.isoformat() if signoff.attested_at else None,
            "stays_above_all_minima": signoff.stays_above_all_minima,
            "with_actions_stays_above_all_minima": (signoff.with_actions_stays_above_all_minima),
        }
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, t1_totals, metadata),
        source_runs=[_source_run_entry(run)],
    )


def _generate_icaap_stress_appendix2(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Universal-bank ICAAP submission in the BoG Appendix II format (¶67, ¶68)."""
    return _generate_attested_appendix2(
        db, ctx, bank, period, definition, include_basel_table2=True
    )


def _generate_sdi_stress_annual(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Proportionate SDI annual stress evidence, without a Basel tier build."""
    generated = _generate_attested_appendix2(
        db, ctx, bank, period, definition, include_basel_table2=False
    )
    generated.snapshot.setdefault("metadata", {})["report_scope"] = (
        "SDI proportionate annual stress packet; Basel Appendix II Table 2 excluded."
    )
    return generated


def _appendix2_governance_section(run: RegulatoryRun, signoff: Any | None) -> dict[str, Any]:
    """The Phase-5 sign-off / Board-attestation facts, on the return itself (¶20)."""
    rows = [
        _appendix2_row(
            "enterprise_stress_run_id",
            "Enterprise-stress run (source)",
            {"value": str(run.id)},
        ),
        _appendix2_row("input_hash", "Enterprise-stress input hash", {"value": run.input_hash}),
    ]
    if signoff is not None:
        # Status in words, and no attester user id: who attested is printed by
        # name and designation in the narrative section (regulatory audit P0R-12,
        # m13) — a raw identifier is not a statement a reader can use.
        rows += [
            _appendix2_row(
                "signoff_status",
                "Board attestation status",
                {"value": _SIGNOFF_STATUS_LABELS.get(signoff.status, _humanised(signoff.status))},
            ),
            _appendix2_row(
                "stays_above_all_minima",
                "Stress case stays above all regulatory minima",
                {"value": _appendix2_bool(signoff.stays_above_all_minima)},
            ),
            _appendix2_row(
                "with_actions_stays_above_all_minima",
                "With management actions, stays above all minima",
                {"value": _appendix2_bool(signoff.with_actions_stays_above_all_minima)},
            ),
        ]
    return _section(
        "governance",
        "Governance — Board Sign-off & Attestation (¶20, ¶57–63)",
        rows,
    )


_SIGNOFF_STATUS_LABELS: dict[str, str] = {
    "draft": "Draft",
    "pending_attestation": "Awaiting Board attestation",
    "attested": "Attested by the Board",
    "withdrawn": "Withdrawn",
}


def _humanised(code: str | None) -> str:
    """A machine code in words ("severely_adverse" -> "Severely adverse")."""
    text = str(code or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _appendix2_bool(value: bool | None) -> str | None:
    if value is None:
        return None
    return "Yes" if value else "No"


#: The attested sign-off's narrative elements, in the order the submission
#: reads them, with the label printed beside each. The paragraph mapping lives
#: on the model (``EnterpriseStressSignoff``) and in the template's citation.
_APPENDIX2_NARRATIVE_ELEMENTS: tuple[tuple[str, str], ...] = (
    (
        "scenario_narrative",
        "Scenario narrative — risks, exposures, entities and macroeconomic conditions "
        "covered (¶67(a)–(b))",
    ),
    (
        "assumptions_rationale",
        "Assumptions rationale — justification of the assumptions and of expert-judgement "
        "overlays (¶67(b), ¶45)",
    ),
    ("methodology_summary", "Methodology summary — methodologies used (¶67(c))"),
    ("board_challenge", "Board challenge (¶20)"),
    (
        "credibility_rationale",
        "Board's rationale for its assessment of the credibility of the framework and "
        "results (¶20)",
    ),
)


def _narrative_text(value: str | None) -> str | None:
    """The recorded text, with line endings normalised; blank is absent (it prints
    "Not stated", never an empty paragraph)."""
    if value is None:
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def _attester_line(db: Session, ctx: TenantContext, signoff: Any) -> str | None:
    """Who attested, by name and designation — not the user id (audit m13).

    Resolved at generation and sealed into the snapshot, so a later change to
    the user's profile cannot rewrite what the filed return says.
    """
    if signoff.attested_by is None:
        return None
    user = db.scalar(
        select(User).where(
            User.id == signoff.attested_by,
            User.organization_id == ctx.organization_id,
        )
    )
    name = (user.display_name or "").strip() if user is not None else ""
    title = (user.job_title or "").strip() if user is not None else ""
    line = f"{name or 'Name not recorded'}, {title or 'designation not recorded'}"
    if signoff.attested_at is not None:
        line += f" (attested {signoff.attested_at.date().isoformat()})"
    return line


def _appendix2_narrative_section(
    db: Session, ctx: TenantContext, signoff: Any
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The Board-attested narrative as a rendered section (ICAAP P0, audit §4.4).

    The narratives were already in the snapshot — ``metadata.governance``, inside
    the content digest the signatures bind — but no artifact printed them, and
    the regulator reads the PDF, not the metadata (Stress Testing Guideline
    ¶67(a)–(c), ¶20). This section carries them as rows the PDF sets as prose.
    An element the sign-off does not record prints "Not stated" and raises a
    validation warning; nothing is paraphrased or filled in.
    """
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for key, label in _APPENDIX2_NARRATIVE_ELEMENTS:
        text = _narrative_text(getattr(signoff, key, None))
        if text is None:
            missing.append(label)
        rows.append(_appendix2_row(key, label, {"value": text}))
    rows.append(
        _appendix2_row("attested_by", "Attested by", {"value": _attester_line(db, ctx, signoff)})
    )
    findings: list[dict[str, str]] = []
    if missing:
        findings.append(
            {
                "rule": "appendix2_narrative_completeness",
                "severity": "WARNING",
                "detail": (
                    "The Board-attested stress sign-off records nothing for: "
                    + "; ".join(missing)
                    + f". The narrative prints '{NOT_STATED}' for each."
                ),
            }
        )
    section = _section(
        "stress_narrative",
        "Stress Test Narrative (Board-attested)",
        rows,
        optional=True,
    )
    return section, findings


#: Every minimum Appendix II's figures are measured against (regulatory audit
#: P0R-5): the capital-requirement lines (CAR, paid-up) and the ¶77 "stays above
#: all minima" verdict (CET1, Tier 1, leverage). Each entry is (control-plane
#: code, where the run records the APPLIED value, printed label, applied unit —
#: ``None`` means the tables' own reporting unit —, Basel regime only).
_APPENDIX2_GOVERNED_MINIMA: tuple[tuple[str, tuple[str, str], str, str | None, bool], ...] = (
    ("car_min", ("table1", "car_target_pct"), "Minimum total capital ratio", "percent", False),
    ("cet1_min", ("thresholds", "cet1_min"), "Minimum CET1 ratio", "percent", True),
    ("tier1_min", ("thresholds", "tier1_min"), "Minimum Tier 1 ratio", "percent", True),
    ("leverage_min", ("thresholds", "leverage_min"), "Minimum leverage ratio", "percent", True),
    (
        "paid_up_min",
        ("table1", "paid_up_min"),
        "Minimum unimpaired paid-up capital",
        None,
        False,
    ),
    # The Table 2 recognition ceilings (D-024 / audit M21), recorded on runs from
    # 2026-09-19; an earlier run carries none and prints no line for them.
    (
        "at1_cap_pct_rwa",
        ("caps", "at1_cap_pct_rwa"),
        "Maximum Additional Tier 1 capital recognised, as a percentage of RWA,",
        "percent",
        True,
    ),
    (
        "tier2_cap_pct_rwa",
        ("caps", "tier2_cap_pct_rwa"),
        "Maximum Tier 2 capital recognised, as a percentage of RWA,",
        "percent",
        True,
    ),
)

_GOVERNED_ROW_FIELDS = (
    "param_code",
    "value",
    "unit",
    "source_citation",
    "confirmation_status",
    "effective_from",
    "scope_type",
    "scope_key",
    "jurisdiction_code",
    "parameter_id",
)

#: The resolver's layer order (``app.domain.policy.resolution_order``): a
#: licence-type row wins over the class row whatever their dates.
_RESOLUTION_LAYER_RANK: dict[str, int] = {"institution_type": 0, "institution_class": 1}


def _governed_row(run: RegulatoryRun, code: str, as_of: date) -> dict[str, Any] | None:
    """The control-plane row that supplied ``code`` to the run, at ``as_of``.

    Read from the run's own ``parameter_provenance`` (audit D-18) — the record,
    sealed beside the run, of WHICH governed rows produced its inputs. That
    ledger is over-inclusive by design, so the row is chosen the way the
    resolver chose it: active on the as-of, the licence-type layer before the
    class layer, then the newest generation. ``None`` when the run predates the
    record or consumed no row for the code.
    """
    candidates: list[dict[str, Any]] = []
    for entry in run.parameter_provenance or []:
        if not isinstance(entry, dict) or entry.get("param_code") != code:
            continue
        try:
            start = date.fromisoformat(str(entry.get("effective_from")))
            raw_end = entry.get("effective_to")
            end = date.fromisoformat(str(raw_end)) if raw_end else None
        except ValueError:
            continue
        if start <= as_of and (end is None or end > as_of):
            candidates.append(entry)
    if not candidates:
        return None
    best_layer = min(
        _RESOLUTION_LAYER_RANK.get(str(entry.get("scope_type")), 2) for entry in candidates
    )
    layer = [
        entry
        for entry in candidates
        if _RESOLUTION_LAYER_RANK.get(str(entry.get("scope_type")), 2) == best_layer
    ]
    newest = max(str(entry["effective_from"]) for entry in layer)
    chosen = min(
        (entry for entry in layer if str(entry["effective_from"]) == newest),
        key=lambda entry: str(entry.get("parameter_id")),
    )
    return {field: chosen.get(field) for field in _GOVERNED_ROW_FIELDS}


#: The capital-ratio minima a capital return prints beside each ratio.
_CAPITAL_RATIO_MINIMA: tuple[tuple[str, str], ...] = (
    ("car_min", "minimum total capital ratio"),
    ("cet1_min", "minimum CET1 ratio"),
    ("tier1_min", "minimum Tier 1 ratio"),
    ("leverage_min", "minimum leverage ratio"),
)


def _percent_text(value: Any) -> str:
    """``"6.500000"`` → ``"6.5%"`` (a governed row's stored scale is not printed)."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return f"{value}%"
    shown = (
        number.quantize(Decimal(1)) if number == number.to_integral_value() else number.normalize()
    )
    return f"{shown:f}%"


def _pending_minimum_notes(run: RegulatoryRun, as_of: date) -> list[str]:
    """One note per capital minimum whose governed row awaits confirmation.

    Founder directive D-024: a ``pending`` value is applied — pending never
    blocks a calculation or a return — but every output that shows it says it
    awaits confirmation. Read from the run's own sealed parameter provenance, so
    the note names the row the arithmetic used.
    """
    notes: list[str] = []
    for code, label in _CAPITAL_RATIO_MINIMA:
        governed = _governed_row(run, code, as_of)
        if governed is None or governed.get("confirmation_status") != "pending":
            continue
        notes.append(
            f"The {label} of {_percent_text(governed.get('value'))} is governed parameter "
            f"{code}, pending confirmation ({governed.get('source_citation', '')}). It is "
            "applied as governed until it is confirmed or changed in the regulatory "
            "parameter set."
        )
    return notes


def _applied_minimum(run: RegulatoryRun, table1: dict[str, Any], source: tuple[str, str]) -> Any:
    where, key = source
    if where == "table1":
        return table1.get(key)
    if where == "caps":
        return ((run.inputs or {}).get("recognition_caps_pct_rwa") or {}).get(key)
    parameters = (run.inputs or {}).get("parameters") or {}
    thresholds = (parameters.get("capital") or {}).get("thresholds_pct") or {}
    return thresholds.get(key)


def _minimum_basis(
    applied: Any, governed: dict[str, Any] | None, unit: str | None, code: str = ""
) -> str | None:
    """How the applied value stands against the governed row — three ways, so a
    weaker value is never described as stricter (security audit M-2). For a
    ceiling (a recognition cap) the stricter value is the LOWER one."""
    if governed is None:
        return "not_recorded"
    if unit != "percent" or governed.get("value") is None:
        return None
    try:
        difference = Decimal(str(applied)) - Decimal(str(governed["value"]))
    except InvalidOperation:
        return None
    if direction_for(code) is Direction.CEILING:
        difference = -difference
    if difference > 0:
        return "stricter_than_governed"
    if difference < 0:
        return "weaker_than_governed"
    return "governed"


def _appendix2_parameter_provenance(
    run: RegulatoryRun,
    table1: dict[str, Any],
    period: BankReportingPeriod,
    unit: str,
    *,
    basel_applicable: bool = True,
) -> list[dict[str, Any]]:
    """Each minimum applied, as the value used plus its authority.

    A list, not a mapping, so the printed order survives any JSON store. The
    CET1 / Tier 1 / leverage minima exist only under the bank (Basel) regime.
    """
    entries: list[dict[str, Any]] = []
    for code, source, label, applied_unit, basel_only in _APPENDIX2_GOVERNED_MINIMA:
        if basel_only and not basel_applicable:
            continue
        applied = _applied_minimum(run, table1, source)
        if applied is None:
            continue
        governed = _governed_row(run, code, period.period_end)
        entries.append(
            {
                "param_code": code,
                "label": label,
                "applied_value": applied,
                "applied_unit": applied_unit or unit,
                "basis": _minimum_basis(applied, governed, applied_unit, code),
                "governed": governed,
            }
        )
    return entries


def _weaker_minimum_findings(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """A WARNING for every minimum the run applied below its governed value."""
    return [
        {
            "rule": "appendix2_minimum_weaker_than_governed",
            "severity": "WARNING",
            "detail": (
                f"The source run applied {entry['label'].lower()} of "
                f"{entry['applied_value']} while the governed parameter "
                f"{entry['param_code']} is {entry['governed']['value']}: the figures "
                "measured against it understate the requirement. Re-run the stress test "
                "under the current governed minima before filing."
            ),
        }
        for entry in entries
        if entry.get("basis") == "weaker_than_governed"
    ]


def _carries_a_verdict(threshold_min: Any, status: str | None) -> bool:
    """True when a stored metric row asserts a compliance verdict.

    A row that states neither a floor nor a classification (``hqla_total_ghs``
    and the other amounts, stored with ``status="na"``) claims nothing about
    compliance, so there is nothing for the authority check to authorise and
    nothing to withhold.
    """
    return threshold_min is not None or (status or UNCLASSIFIED_STATUS) != UNCLASSIFIED_STATUS


def _classified(light: dict[str, Any]) -> bool:
    """True when a traffic-light row PRINTS a classification the pack may act on."""
    return light.get("status") not in (None, "", UNCLASSIFIED_STATUS)


def _stress_traffic_lights(
    db: Session, scenario_runs: list[tuple[str, str, RegulatoryRun]]
) -> list[dict[str, Any]]:
    """One row per stored headline metric of every consumed run — the value
    exactly as the engine persisted it (``RegulatoryMetricResult``); the pack
    classifies nothing itself.

    The threshold and the green/amber/red status are not a value, they are a
    **compliance verdict**, and this pack prints one only where WS-A's metric
    authority register holds a filed regulatory authority for that figure under
    the engine that sealed the row
    (:func:`~app.services.regulatory_reporting.provenance.compliance_verdict_authority`).
    Where it does not, the value still prints and the row states that the
    classification was withheld and why.

    The check lives here, on the READ path, because a sealed run is append-only
    evidence and must not be rewritten. Runs sealed before
    ``regulatory_capital`` stopped persisting a post-stress ``car_pct_end``
    still carry that row with its 10% floor and a green/red status — and they
    keep carrying it, because that is what those runs did. What changes is that
    a filed artifact no longer repeats a verdict no instrument authorises.
    """
    rows: list[dict[str, Any]] = []
    for module, scenario_code, run in scenario_runs:
        results = db.scalars(
            select(RegulatoryMetricResult)
            .where(RegulatoryMetricResult.run_id == run.id)
            .order_by(RegulatoryMetricResult.position)
        ).all()
        rows.extend(_stress_traffic_light(module, scenario_code, result) for result in results)
    return rows


def _stress_traffic_light(
    module: str, scenario_code: str, result: RegulatoryMetricResult
) -> dict[str, Any]:
    """One traffic-light row, with its verdict resolved against the register."""
    threshold = str(result.threshold_min) if result.threshold_min is not None else None
    status = result.status
    basis: str | None = None
    if _carries_a_verdict(result.threshold_min, status):
        verdict = compliance_verdict_authority(result.metric_code, sealed_by=module)
        basis = verdict.basis
        if not verdict.permitted:
            threshold = None
            status = None
    return _row(
        f"{module}:{scenario_code}:{result.metric_code}",
        f"{result.metric_code} under '{scenario_code}' ({module})",
        result.metric_value,
        unit=result.unit,
        threshold=threshold,
        status=status,
        compliance_basis=basis,
        module=module,
        scenario_code=scenario_code,
    )


def _stress_ratio_evolution(capital_runs: dict[str, RegulatoryRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        for quarter in run.metrics.get("stress_path", []):
            rows.append(
                _row(
                    f"{scenario_code}:q{quarter['quarter']}",
                    f"'{scenario_code}' quarter {quarter['quarter']}",
                    quarter["car"],
                    cet1_ratio_pct=quarter["cet1_ratio"],
                    tier1_ratio_pct=quarter["tier1_ratio"],
                    leverage_ratio_pct=quarter["leverage_ratio"],
                    total_rwa_ghs=quarter["total_rwa"],
                    scenario_code=scenario_code,
                )
            )
    return rows


def _stress_pro_forma(capital_runs: dict[str, RegulatoryRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        path = run.metrics.get("stress_path", [])
        if not path:
            continue
        end_state = path[-1]
        rows.append(
            _row(
                scenario_code,
                f"End-state capital position under '{scenario_code}'",
                end_state["total_capital"],
                cet1_capital_ghs=end_state["cet1_capital"],
                tier1_capital_ghs=end_state["tier1_capital"],
                total_rwa_ghs=end_state["total_rwa"],
                credit_rwa_ghs=end_state["credit_rwa"],
                market_rwa_ghs=end_state["market_rwa"],
                operational_rwa_ghs=end_state["operational_rwa"],
                end_car_pct=end_state["car"],
            )
        )
    return rows


def _stress_attribution(
    liquidity_runs: dict[str, RegulatoryRun],
    capital_runs: dict[str, RegulatoryRun],
    liq_baseline: RegulatoryRun,
    cap_baseline: RegulatoryRun,
) -> list[dict[str, Any]]:
    """Scenario-vs-baseline deltas over the SAME canonical book: both runs
    anchor to their engine's baseline input hash, so the delta is pure shock
    attribution rather than a data-vintage difference."""
    rows: list[dict[str, Any]] = []
    for metric in ("lcr_pct", "nsfr_pct"):
        base = Decimal(str(liq_baseline.metrics[metric]))
        for scenario_code, run in sorted(liquidity_runs.items()):
            if scenario_code == BASELINE_SCENARIO or metric not in run.metrics:
                continue
            stressed = Decimal(str(run.metrics[metric]))
            rows.append(
                _row(
                    f"liquidity:{scenario_code}:{metric}",
                    f"{metric} impact of '{scenario_code}' vs baseline",
                    stressed - base,
                    baseline_pct=str(base),
                    stressed_pct=str(stressed),
                    module=MODULE_LIQUIDITY,
                    scenario_code=scenario_code,
                )
            )
    base_car = Decimal(str(cap_baseline.metrics["car_pct"]))
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        path = run.metrics.get("stress_path", [])
        if not path:
            continue
        end_car = Decimal(str(path[-1]["car"]))
        rows.append(
            _row(
                f"capital:{scenario_code}:car_pct",
                f"End-state CAR impact of '{scenario_code}' vs baseline",
                end_car - base_car,
                baseline_pct=str(base_car),
                stressed_pct=str(end_car),
                module=MODULE_CAPITAL,
                scenario_code=scenario_code,
            )
        )
    return rows


def _latest_reverse_stress_run(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> RegulatoryRun | None:
    """The latest succeeded reverse-stress run for the period, or ``None``.

    One lookup for every package that reports reverse stress (the stress pack
    and the ICAAP data companion), so the two can never pick different runs.
    """
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == "reverse_stress",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def _reverse_stress_narrative(reverse_run: RegulatoryRun) -> str:
    """The run's stored narrative, normalised the same way for every package."""
    return str(reverse_run.metrics.get("narrative") or "").strip()


def _reverse_stress_note(frontier_rows: list[dict[str, Any]]) -> str:
    """The reverse-stress result as one printed sentence per axis, in words."""
    parts: list[str] = []
    for row in frontier_rows:
        if row.get("breached") == "true":
            parts.append(
                f"{row['description']}: {row['value']}x (ratio at breach "
                f"{row.get('ratio_at_breach_pct')}% against a {row.get('floor_pct')}% floor)."
            )
        else:
            parts.append(f"{row['description']}.")
    return "Reverse stress test (before management actions): " + " ".join(parts)


def _stress_frontier_rows(reverse_run: RegulatoryRun) -> list[dict[str, Any]]:
    """The reverse-stress frontier, one row per axis, in words.

    The description and the ``scenario`` column name the scaled scenario and the
    floor in words (regulatory audit P0R-11); the machine ``scenario_code`` stays
    on the row for consumers that key on it.
    """
    rows: list[dict[str, Any]] = []
    for axis_code, axis_label, floor_label, floor_key, ratio_key in (
        ("liquidity_frontier", "liquidity", "LCR", "lcr_min_pct", "lcr_at_breach_pct"),
        ("capital_frontier", "capital", "CET1", "cet1_min_pct", "worst_cet1_at_breach_pct"),
    ):
        axis = reverse_run.metrics[axis_code.replace("_frontier", "_axis")]
        breached = axis["breached"]
        scenario = _humanised(axis["scenario_code"])
        rows.append(
            _row(
                axis_code,
                (
                    f"Severity multiplier at which the scaled {scenario} {axis_label} "
                    f"scenario first breaches the {floor_label} floor"
                    if breached
                    else f"No {floor_label} floor breach up to {axis['k_max']}x the "
                    f"{scenario} {axis_label} scenario's severity"
                ),
                axis.get("breach_multiplier", axis.get("k_max", "")),
                breached="true" if breached else "false",
                floor_pct=axis.get(floor_key),
                ratio_at_breach_pct=axis.get(ratio_key),
                scenario_code=axis["scenario_code"],
                scenario=scenario,
            )
        )
    return rows


def _stress_recommended_actions(
    capital_runs: dict[str, RegulatoryRun], traffic_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic remedial actions: the capital engine's own fired triggers
    (which carry prescribed action text) plus a funding-remediation line for
    every liquidity ratio whose PRINTED classification is amber/red. A row whose
    verdict the pack withheld carries no classification and produces no action —
    see :func:`_stress_traffic_lights`."""
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        for trigger in run.metrics.get("triggers", []):
            if not trigger.get("fired"):
                continue
            rows.append(
                _row(
                    f"capital:{scenario_code}:{trigger['code']}",
                    trigger["action"],
                    trigger["threshold_pct"],
                    unit="pct",
                    first_quarter=str(trigger.get("first_quarter") or ""),
                    scenario_code=scenario_code,
                    module=MODULE_CAPITAL,
                )
            )
    for light in traffic_rows:
        if light["module"] != MODULE_LIQUIDITY or light["status"] == "green":
            continue
        # A remedial action is a consequence of a classification, so it may only
        # follow one the pack was authorised to print. A row whose verdict was
        # withheld carries no status, and must not acquire one here by the back
        # door — "restore X above the regulatory minimum" asserts the same
        # unauthorised minimum in prose.
        if not _classified(light):
            continue
        if light["scenario_code"] == BASELINE_SCENARIO:
            continue
        metric = light["code"].rsplit(":", 1)[-1]
        if metric not in ("lcr_pct", "nsfr_pct"):
            continue
        rows.append(
            _row(
                f"liquidity:{light['scenario_code']}:{metric}",
                (
                    f"Restore {metric} above the regulatory minimum: extend the "
                    "high-quality liquid asset buffer or term out the funding "
                    f"profile stressed by scenario '{light['scenario_code']}'."
                ),
                light["value"],
                unit="pct",
                status=light["status"],
                scenario_code=light["scenario_code"],
                module=MODULE_LIQUIDITY,
            )
        )
    return rows


def _generate_template_pending(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Honest refusal for returns whose BoG form is not yet published.

    The obligation is real (it sits in the registry and the calendar), but
    the standing order is to obtain the official form, never infer it — so
    generation names the gap instead of fabricating a layout (product.md
    §Phase 2 items 12/14).
    """
    _ = (db, ctx, bank, period)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "template_pending",
            "message": (
                f"The official form for {definition.code} has not been obtained "
                "yet; this return cannot be generated until the regulator's "
                "template is registered (the layout is never inferred)."
            ),
        },
    )


def _generate_stress_pack(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Stress Test Output Report pack (product.md §Phase 2 item 6).

    Assembles ONLY stored engine state — the latest succeeded liquidity and
    capital runs per scenario, their persisted headline metric results, and
    the latest reverse-stress frontier — into the standardized Board/ALCO
    output report: traffic lights, ratio evolution, pro-forma capital,
    baseline attribution, recommended actions.
    """
    liq_baseline = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_LIQUIDITY, artifact="the stress pack"
    )
    cap_baseline = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_CAPITAL, artifact="the stress pack"
    )
    liquidity_runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    capital_runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_CAPITAL)
    scenario_runs = [
        (MODULE_LIQUIDITY, code, run) for code, run in sorted(liquidity_runs.items())
    ] + [(MODULE_CAPITAL, code, run) for code, run in sorted(capital_runs.items())]
    stressed = [entry for entry in scenario_runs if entry[1] != BASELINE_SCENARIO]
    if not stressed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_stress_scenarios",
                "message": (
                    "No succeeded stress-scenario runs exist for this reporting "
                    "period. Run at least one non-baseline liquidity or capital "
                    "scenario before generating the stress pack."
                ),
            },
        )

    reverse_run = _latest_reverse_stress_run(db, ctx, bank, period)

    traffic_rows = _stress_traffic_lights(db, scenario_runs)
    evolution_rows = _stress_ratio_evolution(capital_runs)
    pro_forma_rows = _stress_pro_forma(capital_runs)
    attribution_rows = _stress_attribution(liquidity_runs, capital_runs, liq_baseline, cap_baseline)
    frontier_rows = _stress_frontier_rows(reverse_run) if reverse_run is not None else []
    action_rows = _stress_recommended_actions(capital_runs, traffic_rows)

    sections = [
        _section("traffic_lights", "Stress Outcome Traffic Lights", traffic_rows),
        _section(
            "ratio_evolution",
            "Capital Ratio Evolution Under Stress",
            evolution_rows,
            optional=True,
        ),
        _section(
            "pro_forma_capital",
            "Pro-Forma Capital Position (Stress End-State)",
            pro_forma_rows,
            optional=True,
        ),
        _section("attribution", "Scenario Attribution vs Baseline", attribution_rows),
        _section(
            "reverse_stress_frontier",
            "Reverse-Stress Frontier",
            frontier_rows,
            optional=True,
        ),
        _section("recommended_actions", "Recommended Actions", action_rows, optional=True),
    ]

    stressed_lcrs = [
        Decimal(str(run.metrics["lcr_pct"]))
        for code, run in liquidity_runs.items()
        if code != BASELINE_SCENARIO and "lcr_pct" in run.metrics
    ]
    end_cars = [
        Decimal(str(run.metrics["stress_path"][-1]["car"]))
        for code, run in capital_runs.items()
        if code != BASELINE_SCENARIO and run.metrics.get("stress_path")
    ]
    red_count = sum(1 for light in traffic_rows if light["status"] == "red")
    totals = [_row("red_light_count", "Metrics classified red across scenarios", red_count)]
    if stressed_lcrs:
        totals.append(
            _row(
                "worst_stressed_lcr_pct",
                "Worst stressed LCR across scenarios",
                min(stressed_lcrs),
            )
        )
    if end_cars:
        totals.append(
            _row("worst_end_car_pct", "Worst end-state CAR across scenarios", min(end_cars))
        )

    metadata: dict[str, Any] = {
        "liquidity_scenarios": sorted(liquidity_runs),
        "capital_scenarios": sorted(capital_runs),
        "baseline_liquidity_input_hash": liq_baseline.input_hash,
        "baseline_capital_input_hash": cap_baseline.input_hash,
        "fired_trigger_count": sum(
            1
            for code, run in capital_runs.items()
            if code != BASELINE_SCENARIO
            for trigger in run.metrics.get("triggers", [])
            if trigger.get("fired")
        ),
    }
    if reverse_run is not None:
        metadata["reverse_stress_run_id"] = str(reverse_run.id)
        metadata["reverse_stress_narrative"] = _reverse_stress_narrative(reverse_run)

    source_runs = [_source_run_entry(run) for _, _, run in scenario_runs]
    if reverse_run is not None:
        source_runs.append(_source_run_entry(reverse_run))
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=source_runs,
    )


# Public seams for sibling generator modules: the LRT corporate packs (plan
# W5, ``lrt_generation.py``) and the W6 canonical-position returns
# (``le_generation.py``) assemble the same regulatory-package-v1
# envelope/section/row shapes without reaching into private helpers.
snapshot_row = _row
snapshot_section = _section
snapshot_total = _total
headline_comparative_section = _headline_comparative_section
build_envelope = _envelope
baseline_run_or_409 = _baseline_run_or_409
source_run_entry = _source_run_entry
latest_succeeded_runs_by_scenario = _latest_succeeded_runs_by_scenario
lcr_snapshot_sections = _lcr_sections
lcr_snapshot_totals = _lcr_totals
liquidity_snapshot_metadata = _liquidity_metadata

# Imported at the bottom — after the seams above are defined — so the module
# pairs load cleanly regardless of which side is imported first.
from app.services.regulatory_reporting.bog_forms.generation import BOG_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.dbk_generation import DBK_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.le_generation import LE_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.lrt_generation import LRT_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.npl_generation import NPL_GENERATORS  # noqa: E402

_GENERATORS = {
    "liquidity": _generate_liquidity,
    "capital": _generate_capital,
    "irrbb": _generate_irrbb,
    "sdi_irrbb": _generate_sdi_irrbb,
    "fx": _generate_fx,
    "icaap_stress": _generate_icaap_stress,
    "icaap_stress_appendix2": _generate_icaap_stress_appendix2,
    "sdi_stress_annual": _generate_sdi_stress_annual,
    "stress_pack": _generate_stress_pack,
    "template_pending": _generate_template_pending,
    **LRT_GENERATORS,
    **NPL_GENERATORS,
    **LE_GENERATORS,
    **DBK_GENERATORS,
    **BOG_GENERATORS,
}

__all__ = [
    "GeneratedReturn",
    "SNAPSHOT_SCHEMA_VERSION",
    "generate_package",
]
