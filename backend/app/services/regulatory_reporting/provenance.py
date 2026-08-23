"""Reporting provenance — who is the authority for every figure in a package.

The 2026-08-21 forensic architecture audit (§8, §10 item 3) found that
``bog_form`` returns resolve fact/position/reference sources into official
template input cells, evaluate the workbook's own formulas, and then return
``source_runs=[]``. Nothing in the record said *why* the list was empty, and
nothing distinguished "no engine produced this number because BoG's template
produced it" from "provenance was dropped". The audit's verdict:

    "the architecture has no universal 'metric authority registry' to make
    this explicit or fail when an engine/template mapping diverges."

This module is the reporting half of the answer. It does **not** invent a
second provenance store: run provenance is read through WS-A's
:class:`app.domain.authority.provenance.CalculationProvenance`, whose
``source_run_entry()`` is byte-identical to the ``source_runs`` shape the
package column has always carried, and metric ownership is read through WS-A's
:data:`app.domain.authority.registry.REGISTRY`.

What it adds is the missing declaration: for every package, an explicit
:class:`ReportAuthority` plus the versioned identity of everything that stood
between the canonical data and the filed cell — the line-map (mapping) digest,
the committed official-template digest, the formula-evaluator version, the
parameter digest and the effective date.

The authorities
---------------

:attr:`ReportAuthority.TEMPLATE_FORMULA`
    BoG's own workbook formula owns the figure. This is the case the audit
    asked to be made explicit: an empty ``source_runs`` on a BSD form is a
    *statement*, not an omission. The template's formulas are authoritative and
    are never re-implemented (``bog_forms/formulas.py`` evaluates them).

:attr:`ReportAuthority.TEMPLATE_INPUT_MAPPING`
    An official INPUT cell fed by a named canonical resolver
    (``facts.sum`` / ``positions.sum`` / ``refs.*`` / ``form.cell`` / …). The
    template owns the *structure*; the platform owns the *mapping*, which is why
    ``mapping_version`` exists.

:attr:`ReportAuthority.ENGINE_RUN`
    A sealed :class:`RegulatoryRun` owns the figure — the run-backed packages
    (LCR-NSFR, CAR-RWA, IRRBB-PILOT, FX-NOP, DBK-DAILY, the stress packs) and
    the individual BSD lines bound to ``run.metric`` / ``bsd5.run_line`` / the
    FX-run resolvers.

:attr:`ReportAuthority.GUIDE_INSTRUCTION`
    The Guide prescribes the arithmetic and the official template carries **no
    formula cell** for it, so the platform computes it under an explicit
    citation. Exactly one binding is in this class today: BSD2A's "%age of
    exposure to net worth" column (audit finding CF-3) — the BSD2A layout is a
    blank grid, so there is no BoG formula to evaluate and none may be invented.

:attr:`ReportAuthority.MASTER_DATA_REGISTER`
    Corporate/master-data packs (the LRT family) that bind no engine run at all.
    Their counterpart seal is ``register_state_digest``.

:attr:`ReportAuthority.NOT_YET_SOURCED`
    The official cell exists and is declared, but no honest source feeds it yet
    (``input_required`` / ``unmapped``). Never silently dropped, never zero-filled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from app.domain.authority.provenance import CalculationProvenance
from app.domain.authority.registry import (
    EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
    REGISTRY,
    AdvisoryDesignation,
    MetricAuthority,
    UnknownMetricError,
)

__all__ = [
    "ENGINE_BACKED_RESOLVERS",
    "FORMULA_EVALUATOR_VERSION",
    "MAPPING_SCHEMA_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "RESOLVER_AUTHORITY",
    "UNCLASSIFIED_STATUS",
    "ComplianceVerdictAuthority",
    "ReportAuthority",
    "ReportingProvenance",
    "authority_for_resolver",
    "compliance_verdict_authority",
    "declared_methodology_notes",
    "line_status_authority",
    "mapping_digest",
    "run_provenance_entry",
    "source_run_entry",
    "template_digest",
]


#: Bumped whenever the shape of the ``snapshot["provenance"]`` block changes.
PROVENANCE_SCHEMA_VERSION = "reporting-provenance-v1"

#: The workbook evaluator's supported vocabulary (SUM / IF / + - * / % / cell
#: refs / ranges / ``[n]Sheet!`` external links). Any change to
#: ``bog_forms/formulas.py`` that changes an evaluated value must bump this —
#: it is the version of the thing that turned BoG's formulas into numbers.
FORMULA_EVALUATOR_VERSION = "bog-workbook-evaluator-v1"

#: The shape of the line-map digest below (not the content — that is the digest).
MAPPING_SCHEMA_VERSION = "bog-linemap-v1"

#: Recorded where a figure genuinely has no versioned parameter set behind it
#: (template evaluation and master-data packs consume no governed parameters).
NO_PARAMETERS = "not_applicable:no_governed_parameters"


class ReportAuthority(StrEnum):
    """Who owns a reported figure. See the module docstring for each case."""

    ENGINE_RUN = "engine_run"
    TEMPLATE_FORMULA = "template_formula"
    TEMPLATE_INPUT_MAPPING = "template_input_mapping"
    GUIDE_INSTRUCTION = "guide_instruction"
    MASTER_DATA_REGISTER = "master_data_register"
    NOT_YET_SOURCED = "not_yet_sourced"


#: Every registered ``bog_forms`` resolver → the authority that owns the value it
#: returns. The mapping is exhaustive by test
#: (``test_reporting_provenance.py::test_every_resolver_declares_an_authority``):
#: adding a resolver without classifying it fails, so the gap the audit found
#: cannot silently reopen.
RESOLVER_AUTHORITY: dict[str, ReportAuthority] = {
    # -- canonical data mapped into official INPUT cells --------------------
    "constant": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "facts.sum": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "positions.sum": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "refs.sum": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "refs.count": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "refs.field": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "form.cell": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd1.daily": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd1.fx_spot": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd11.register": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd13.positions_ccy": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd13.forward_contract": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd14.column_constant": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd14.rate": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd1a.rank": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd3.count": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd3.rank": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd4.annex4a": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd4.annex4b": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd4.cell": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd5.balance_sheet_side": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd5.capital_facts": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd5.off_balance_residual": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd5.pct_of": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd6.bucket": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd7.average_facts": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd7.pl_line": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd8.annexure": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd8.bucket": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    "bsd9.bsd2_lines": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    # A selection/aggregation of already-computed BSD2 dependency cells. It
    # applies no new BoG rule, so it stays an input mapping.
    "bsd2a.form_cells_sum": ReportAuthority.TEMPLATE_INPUT_MAPPING,
    # -- figures a sealed engine run owns ----------------------------------
    "run.metric": ReportAuthority.ENGINE_RUN,
    "bsd5.run_line": ReportAuthority.ENGINE_RUN,
    "bsd5.avg_gross_income": ReportAuthority.ENGINE_RUN,
    "bsd13.nop": ReportAuthority.ENGINE_RUN,
    "bsd1b.nop": ReportAuthority.ENGINE_RUN,
    # -- audit finding CF-3: computed on render under a Guide citation ------
    # BSD2A's official layout is a BLANK grid (no input cells, no formula
    # cells), so the "%age of exposure to net worth" column has no BoG formula
    # to evaluate. Guide BSD2A ¶5(vii) prescribes the arithmetic in words; the
    # platform applies it and says so here rather than implying, through an
    # empty source_runs, that BoG's template produced it.
    "bsd2a.form_cells_ratio_pct": ReportAuthority.GUIDE_INSTRUCTION,
}

#: The resolvers whose value is owned by a sealed run. These are the exact
#: BoG-form / engine overlap points, and therefore the population the reporting
#: equivalence gate must prove against its source run.
ENGINE_BACKED_RESOLVERS: frozenset[str] = frozenset(
    name
    for name, authority in RESOLVER_AUTHORITY.items()
    if authority is ReportAuthority.ENGINE_RUN
)

#: Citations for the authorities that are not "a run produced it".
AUTHORITY_BASIS: dict[ReportAuthority, str] = {
    ReportAuthority.TEMPLATE_FORMULA: (
        "The official Bank of Ghana workbook is the authority for every derived figure on "
        "this return: the template's own formula cells are evaluated as published "
        "(bog_forms/formulas.py) and are never re-implemented or simplified. No calculation "
        "run stands behind these figures, which is why source_runs is empty."
    ),
    ReportAuthority.TEMPLATE_INPUT_MAPPING: (
        "Official INPUT cells of the Bank of Ghana template, filled from canonical data "
        "through named resolvers. The template owns the structure; the line map owns the "
        "binding, and its version is recorded as mapping_version."
    ),
    ReportAuthority.ENGINE_RUN: (
        "Sealed calculation runs are the authority: every figure traces to a RegulatoryRun "
        "listed in source_runs by module, run id, value-based input hash and engine version."
    ),
    ReportAuthority.GUIDE_INSTRUCTION: (
        "The Guide for Reporting Institutions prescribes this figure in words and the "
        "official template carries no formula cell for it (the sheet is a blank grid), so "
        "the platform computes it under the cited paragraph. No BoG formula was replaced "
        "and none was invented."
    ),
    ReportAuthority.MASTER_DATA_REGISTER: (
        "A corporate/master-data pack: it binds no calculation run by design, so source_runs "
        "is empty and the figures are sealed by register_state_digest instead."
    ),
    ReportAuthority.NOT_YET_SOURCED: (
        "The official cell is declared but no honest source feeds it yet; it exports blank "
        "rather than zero-filled."
    ),
}

#: Why ``source_runs`` is empty, per authority. Written into the package so an
#: examiner reading the record is told, rather than left to infer.
SOURCE_RUNS_RATIONALE: dict[ReportAuthority, str] = {
    ReportAuthority.TEMPLATE_FORMULA: (
        "template-authoritative: this return is generated by evaluating the official Bank of "
        "Ghana workbook's own formulas over official input cells, so no calculation run "
        "stands behind it. The template digest, line-map version and formula-evaluator "
        "version below are its provenance."
    ),
    ReportAuthority.MASTER_DATA_REGISTER: (
        "master-data-authoritative: this pack reports registered corporate master data and "
        "binds no calculation run; register_state_digest seals what was reported."
    ),
}


# ---------------------------------------------------------------------------
# versioned identities
# ---------------------------------------------------------------------------


def _canonical_json(payload: Any) -> str:
    """Value-based canonical JSON — the same discipline as ``input_hash``."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@cache
def template_digest(form_code: str) -> str:
    """SHA-256 of the committed official layout for ``form_code``.

    The layout JSON under ``bog_forms/layouts/`` **is** the official structure
    (regenerated only by ``scripts/extract_bog_templates.py`` from the workbooks
    in ``docs/reporting/``). Hashing the file bytes gives the filed package a
    stable identity for "which published template produced this", so a template
    regeneration is visible in the record instead of silent.

    Returns ``""`` for a return with no committed layout (the run-backed and
    master-data families), never a fabricated digest.
    """
    from app.services.regulatory_reporting.bog_forms.layout import LAYOUT_DIR  # noqa: PLC0415

    path = Path(LAYOUT_DIR) / f"{form_code}.json"
    if not path.exists():
        return ""
    return "sha256:" + _sha256(path.read_bytes().hex())


@cache
def mapping_digest(form_code: str) -> str:
    """SHA-256 over the form's line map — the binding of sources to official cells.

    Value-based over ``(sheet, code, column, cell, source, params, unscaled)``
    so re-ordering a line map does not move the digest but re-pointing a cell
    does. This is the ``mapping_version`` the audit asked for: the thing that
    changes when the platform decides a different canonical source feeds an
    official cell.

    Returns ``""`` when the return has no form spec (run-backed families).
    """
    from app.services.regulatory_reporting.bog_forms.catalog import form_spec  # noqa: PLC0415

    try:
        spec = form_spec(form_code)
    except (KeyError, FileNotFoundError):
        return ""
    payload = sorted(
        [
            sheet.name,
            line.code,
            column,
            ref,
            line.source or "",
            _canonical_json(dict(line.params)),
            bool(line.unscaled),
        ]
        for sheet in spec.sheets
        for line in sheet.lines
        for column, ref in line.cells.items()
    )
    return "sha256:" + _sha256(_canonical_json(payload))


def authority_for_resolver(source: str | None) -> ReportAuthority:
    """The authority owning a value produced by ``source``.

    An unclassified resolver is a defect, not a default: it raises rather than
    quietly claiming template authority for something nobody reviewed.
    """
    if source is None or not source:
        return ReportAuthority.NOT_YET_SOURCED
    try:
        return RESOLVER_AUTHORITY[source]
    except KeyError as exc:  # pragma: no cover - the exhaustiveness test catches this first
        msg = (
            f"resolver {source!r} declares no reporting authority; add it to "
            "regulatory_reporting.provenance.RESOLVER_AUTHORITY so every filed figure "
            "states who owns it"
        )
        raise KeyError(msg) from exc


def line_status_authority(status: str, source: str | None) -> ReportAuthority:
    """The authority to stamp on one resolved template line."""
    if status in ("input_required", "unmapped"):
        return ReportAuthority.NOT_YET_SOURCED
    return authority_for_resolver(source)


# ---------------------------------------------------------------------------
# may this figure carry a COMPLIANCE VERDICT?
# ---------------------------------------------------------------------------

#: The status a sealed ``RegulatoryMetricResult`` carries when its engine made no
#: compliance assessment at all — an amount rather than a ratio with a floor
#: (``hqla_total_ghs``, ``asf_total_ghs``, …). Not a verdict, so nothing about it
#: is withheld.
UNCLASSIFIED_STATUS = "na"

#: Plain-English name for the sealing engine, for the withheld sentence a Board
#: reader sees. An unmapped module degrades to its own token with the underscores
#: removed rather than to a guess.
_SEALING_ENGINE_LABELS: Mapping[str, str] = {
    "capital": "capital",
    "liquidity": "liquidity",
    "irr": "interest-rate risk",
    "fx": "foreign-exchange",
    "ftp": "funds-transfer pricing",
    "forecast": "forecasting",
    "reverse_stress": "reverse-stress",
}


def _sealing_engine_label(sealed_by: str) -> str:
    return _SEALING_ENGINE_LABELS.get(sealed_by, sealed_by.replace("_", " "))


@dataclass(frozen=True, slots=True)
class ComplianceVerdictAuthority:
    """Whether a reported figure may carry a threshold and a pass/fail status.

    A value and a *verdict on that value* are two different claims. Reporting a
    number says the engine computed it; printing a minimum beside it and a
    green/amber/red status says a regulator set that minimum and this figure is
    required to meet it. Only the second needs an authority, and until 2026-08-23
    nothing in the reporting path checked for one — a metric row's stored
    ``threshold_min`` and ``status`` were copied into a filed artifact verbatim,
    so whatever an engine happened to persist became a regulatory assertion.

    ``permitted`` False never suppresses the value. It suppresses the verdict and
    replaces it with :attr:`withheld_reason`, so the artifact states that a
    classification was withheld and why, instead of showing a blank a reader
    would take for "not measured".
    """

    metric_id: str
    sealed_by: str
    permitted: bool
    authority_reference: str | None = None
    methodology_id: str | None = None
    withheld_reason: str = ""

    @property
    def basis(self) -> str | None:
        """The one line the artifact prints beside the figure.

        The citation that authorises the verdict, or the sentence explaining why
        there is none. Never a metric code, an enum value or a module name.
        """
        return self.authority_reference if self.permitted else (self.withheld_reason or None)


def compliance_verdict_authority(metric_id: str, *, sealed_by: str) -> ComplianceVerdictAuthority:
    """May a figure named ``metric_id``, sealed by run module ``sealed_by``, be
    reported with a threshold and a compliance classification?

    Resolved against WS-A's metric authority register — the same register
    :func:`declared_methodology_notes` reads — rather than against a list of
    metric names kept beside the reporting code. A verdict is permitted only when
    the register holds an authority that is, all three:

    * **for this metric** — registered at all. An unregistered figure has no
      declared owner, so nothing establishes a minimum for it;
    * **sealed by this engine** — ``authoritative_run_type`` equals the run module
      that persisted the row, so a figure reaches a filing under the engine that
      actually owns it and not under one that merely emitted the same name;
    * **filed, under an established citation** —
      :attr:`AdvisoryDesignation.FILED` (``advisory_only`` says in the register's
      own words that it *"must never reach a filing"*, and
      ``supervisory_monitoring`` is reviewed but not filed) and not carrying
      :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED`, which is the register's
      honest declaration that this repository establishes no legal basis.

    Deliberately NOT a test: ``instrument_in_force``. Several entries cite
    paragraphs of instruments that are published but have not commenced, and the
    register documents that building against them is a deliberate product choice,
    not a defect. Refusing those here would overturn that decision silently.

    This function invents nothing. Every citation it returns is the register's own
    ``authority_reference`` string.
    """
    try:
        candidates = REGISTRY.for_metric(metric_id)
    except UnknownMetricError:
        candidates = ()
    if not candidates:
        return ComplianceVerdictAuthority(
            metric_id=metric_id,
            sealed_by=sealed_by,
            permitted=False,
            withheld_reason=(
                "Reported for information only — no regulatory authority is recorded "
                "for this figure, so the pack shows no minimum and no compliance "
                "classification for it."
            ),
        )
    filed = [
        entry
        for entry in candidates
        if entry.authoritative_run_type == sealed_by
        and entry.advisory_designation is AdvisoryDesignation.FILED
    ]
    if not filed:
        return ComplianceVerdictAuthority(
            metric_id=metric_id,
            sealed_by=sealed_by,
            permitted=False,
            withheld_reason=(
                "Reported for information only — this figure is not a filed "
                f"regulatory measure of the {_sealing_engine_label(sealed_by)} "
                "calculation, so the pack shows no minimum and no compliance "
                "classification for it."
            ),
        )
    cited = [entry for entry in filed if not entry.requires_external_verification]
    if not cited:
        return ComplianceVerdictAuthority(
            metric_id=metric_id,
            sealed_by=sealed_by,
            permitted=False,
            withheld_reason=(
                "Reported for information only — no verified regulatory citation "
                "establishes a minimum for this figure, so the pack shows no "
                "compliance classification for it."
            ),
        )
    # Deterministic pick: the primary methodology, then alphabetical. Every
    # survivor is filed and cited, so the choice is which citation to PRINT, not
    # whether the verdict stands.
    match = min(cited, key=lambda entry: (not entry.is_primary, entry.methodology_id))
    return ComplianceVerdictAuthority(
        metric_id=metric_id,
        sealed_by=sealed_by,
        permitted=True,
        authority_reference=match.authority_reference,
        methodology_id=match.methodology_id,
    )


# ---------------------------------------------------------------------------
# run provenance (through WS-A's CalculationProvenance — not a second store)
# ---------------------------------------------------------------------------


def calculation_provenance(run: Any) -> CalculationProvenance:
    """WS-A's formal provenance view of one run — the single construction site.

    Exposed so callers that must ENFORCE provenance completeness
    (``require_complete()``) do not each rebuild the view, and so the enforcement
    and the recording read the same object.
    """
    return CalculationProvenance.from_run(run)


def source_run_entry(run: Any) -> dict[str, Any]:
    """The existing ``source_runs`` entry, built through WS-A's primitive.

    Byte-identical to the shape ``RegulatoryPackage.source_runs`` has always
    carried ``{module, run_id, input_hash, engine_version}``, so adopting the
    formal provenance interface needs no migration and moves no digest.
    """
    return CalculationProvenance.from_run(run).source_run_entry()


def run_provenance_entry(run: Any) -> dict[str, Any]:
    """The FULL provenance of one source run, for the package's authority block.

    Everything here already exists on the run; nothing is stored twice.
    ``parameter_digest`` is WS-A's derived identity for the governed parameter
    VALUES (``RegulatoryRun`` has no parameter-set-version column, and inventing
    one was rejected). ``parameter_rows_digest`` is the audit-D-18 companion: a
    fingerprint of WHICH governed ``regulatory_parameter`` rows the run resolved,
    read from the run's own ``parameter_provenance``. It is ``None`` on a run
    minted before ``202608230039`` — unrecorded, which is not the same claim as
    "none used" and must not be rendered as one.
    """
    prov = CalculationProvenance.from_run(run)
    return {
        "module": prov.module,
        "run_id": prov.run_id,
        "input_hash": prov.input_hash,
        "engine_version": prov.engine_version,
        "calculation_version": prov.engine_version,
        "input_schema_version": prov.input_schema_version,
        "output_schema_version": prov.output_schema_version,
        "parameter_digest": prov.parameter_digest,
        "parameter_rows_digest": prov.parameter_rows_digest,
        "scenario_code": prov.scenario_code,
        "reporting_period_id": prov.reporting_period_id,
        "computed_at": prov.computed_at.isoformat() if prov.computed_at is not None else None,
        "actor_id": prov.actor_id,
        "jurisdiction_code": prov.jurisdiction_code,
        "base_currency": prov.base_currency,
        "as_of_date": prov.as_of_date,
        "provenance_complete": prov.is_complete,
        "filable": prov.is_filable,
        "missing_provenance_fields": list(prov.missing_fields()),
    }


def declared_methodology_notes(
    declared: Mapping[str, str] | Sequence[tuple[str, str]] | None,
) -> list[dict[str, Any]]:
    """Resolve a return's declared ``{metric_id: methodology_id}`` against WS-A.

    This is the audit's "which ``lcr_pct`` does this surface mean?" answered in
    the filed record. Where WS-A's registry knows the metric, the entry carries
    the owning engine, regime, citation and any documented divergence from the
    alternate methodology; where it does not yet, the entry says so with the
    registry's own sentinel instead of guessing.
    """
    pairs = dict(declared) if declared else {}
    notes: list[dict[str, Any]] = []
    for metric_id, methodology_id in sorted(pairs.items()):
        try:
            candidates = REGISTRY.for_metric(metric_id)
        except KeyError:
            candidates = ()
        match: MetricAuthority | None = next(
            (entry for entry in candidates if entry.methodology_id == methodology_id), None
        )
        note: dict[str, Any] = {
            "metric_id": metric_id,
            "methodology_id": methodology_id,
            "alternate_methodologies": sorted(
                entry.methodology_id
                for entry in candidates
                if entry.methodology_id != methodology_id
            ),
        }
        if match is None:
            note["registry_status"] = "not_registered"
            note["authority_reference"] = EXTERNAL_REGULATORY_VERIFICATION_REQUIRED
        else:
            note["registry_status"] = "registered"
            note["regime"] = match.regime.value
            note["calculation_engine"] = match.calculation_engine
            note["calculation_version"] = match.calculation_version
            note["authority_reference"] = match.authority_reference
            note["advisory_designation"] = match.advisory_designation.value
            note["expected_tolerance"] = (
                str(match.expected_tolerance) if match.expected_tolerance is not None else None
            )
            if match.divergence is not None:
                note["divergence"] = match.divergence.to_dict()
        notes.append(note)
    return notes


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportingProvenance:
    """The authority record embedded in every package snapshot.

    One instance answers, for a whole return: who owns the figures, which
    sealed runs (if any) produced them and why the list is empty when it is,
    which canonical generation and parameter set fed them, which line map bound
    them to which published template, which evaluator turned BoG's formulas into
    numbers, and as of when.
    """

    return_code: str
    return_family: str
    regulator: str
    authority: ReportAuthority
    effective_date: date
    template_id: str
    #: per-field authority tallies (``ReportAuthority`` value -> count)
    authority_counts: dict[str, int] = field(default_factory=dict)
    source_runs: list[dict[str, Any]] = field(default_factory=list)
    source_runs_rationale: str | None = None
    calculation_version: str = ""
    parameter_version: str = NO_PARAMETERS
    policy_resolution: dict[str, Any] = field(default_factory=dict)
    fact_generation: dict[str, Any] = field(default_factory=dict)
    mapping_version: str = ""
    template_hash: str = ""
    official_workbook: str = ""
    formula_evaluator_version: str = ""
    formula_cells_evaluated: int | None = None
    declared_methodologies: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "return_code": self.return_code,
            "return_family": self.return_family,
            "regulator": self.regulator,
            "authority": self.authority.value,
            "authority_basis": AUTHORITY_BASIS[self.authority],
            "authority_counts": dict(sorted(self.authority_counts.items())),
            "effective_date": self.effective_date.isoformat(),
            "template_id": self.template_id,
            "source_runs": list(self.source_runs),
            "source_runs_rationale": self.source_runs_rationale,
            "calculation_version": self.calculation_version,
            "parameter_version": self.parameter_version,
            "policy_resolution": dict(self.policy_resolution),
            "fact_generation": dict(self.fact_generation),
            "mapping_version": self.mapping_version,
            "mapping_schema_version": MAPPING_SCHEMA_VERSION,
            "template_hash": self.template_hash,
            "official_workbook": self.official_workbook,
            "formula_evaluator_version": self.formula_evaluator_version,
            "formula_cells_evaluated": self.formula_cells_evaluated,
            "declared_methodologies": list(self.declared_methodologies),
        }

    @property
    def is_template_authoritative(self) -> bool:
        return self.authority is ReportAuthority.TEMPLATE_FORMULA


def _policy_resolution(bank: Any, runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How jurisdiction/currency/scenario policy resolved for this package.

    Read from the bank row and from what the runs actually consumed — never a
    literal (docs/CLAUDE.md: jurisdiction is data).
    """
    return {
        "jurisdiction_code": bank.jurisdiction_code,
        "base_currency": bank.currency,
        "institution_type": getattr(bank, "institution_type", None),
        "run_jurisdictions": sorted(
            {
                str(entry.get("jurisdiction_code"))
                for entry in runs
                if entry.get("jurisdiction_code")
            }
        ),
        "scenarios": sorted(
            {str(entry.get("scenario_code")) for entry in runs if entry.get("scenario_code")}
        ),
    }


def _fact_generation(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The canonical fact generation the figures were derived from.

    The live engine re-derives facts (new UUIDs) on every refresh, so the honest
    identity of "which facts" is the value-based ``input_hash`` plus the fact
    snapshot schema version — never a row id (CLAUDE.md pins this).
    """
    return {
        "input_schema_versions": sorted(
            {
                str(entry.get("input_schema_version"))
                for entry in runs
                if entry.get("input_schema_version")
            }
        ),
        "input_hashes": sorted(
            {str(entry.get("input_hash")) for entry in runs if entry.get("input_hash")}
        ),
        "as_of_dates": sorted(
            {str(entry.get("as_of_date")) for entry in runs if entry.get("as_of_date")}
        ),
    }


def build_engine_provenance(
    *,
    definition: Any,
    bank: Any,
    effective_date: date,
    runs: Sequence[Any],
    authority: ReportAuthority = ReportAuthority.ENGINE_RUN,
) -> ReportingProvenance:
    """Provenance for a package whose figures a sealed run owns."""
    entries = [run_provenance_entry(run) for run in runs]
    calculation_versions = sorted({str(entry["engine_version"]) for entry in entries})
    parameter_digests = sorted({str(entry["parameter_digest"]) for entry in entries})
    return ReportingProvenance(
        return_code=definition.code,
        return_family=definition.family,
        regulator=definition.regulator,
        authority=authority,
        effective_date=effective_date,
        template_id=definition.template_id,
        source_runs=entries,
        source_runs_rationale=(None if entries else SOURCE_RUNS_RATIONALE.get(authority)),
        calculation_version=" · ".join(calculation_versions),
        parameter_version=(" · ".join(parameter_digests) if parameter_digests else NO_PARAMETERS),
        policy_resolution=_policy_resolution(bank, entries),
        fact_generation=_fact_generation(entries),
        declared_methodologies=declared_methodology_notes(
            getattr(definition, "declared_methodologies", None)
        ),
    )


def build_template_provenance(  # noqa: PLR0913 — one keyword per provenance dimension
    *,
    definition: Any,
    bank: Any,
    effective_date: date,
    form_code: str,
    workbook: str,
    authority_counts: Mapping[str, int],
    formula_cells_evaluated: int,
) -> ReportingProvenance:
    """Provenance for a ``bog_form`` return — the template-authoritative case.

    ``source_runs`` stays empty because that is the truth: no engine run
    produced these figures. What replaces it is stated, not implied — the
    committed template digest, the line-map digest, and the evaluator version
    that applied BoG's own formulas.
    """
    return ReportingProvenance(
        return_code=definition.code,
        return_family=definition.family,
        regulator=definition.regulator,
        authority=ReportAuthority.TEMPLATE_FORMULA,
        effective_date=effective_date,
        template_id=definition.template_id,
        authority_counts=dict(authority_counts),
        source_runs=[],
        source_runs_rationale=SOURCE_RUNS_RATIONALE[ReportAuthority.TEMPLATE_FORMULA],
        calculation_version=FORMULA_EVALUATOR_VERSION,
        parameter_version=NO_PARAMETERS,
        policy_resolution=_policy_resolution(bank, ()),
        fact_generation={},
        mapping_version=mapping_digest(form_code),
        template_hash=template_digest(form_code),
        official_workbook=workbook,
        formula_evaluator_version=FORMULA_EVALUATOR_VERSION,
        formula_cells_evaluated=formula_cells_evaluated,
        declared_methodologies=declared_methodology_notes(
            getattr(definition, "declared_methodologies", None)
        ),
    )
