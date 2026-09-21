"""The governed numbers the ICAAP workspace reads, and how it refuses without them.

Founder directive D-024: "Don't hardcode any number but fetch from console."
Every ICAAP deadline, amber window, materiality threshold and checklist horizon
is a row in the regulatory-parameter control plane that staff propose and
approve in the operator console, effective-dated. This module is the only place
in the ICAAP code that knows those codes exist, and the only place values
appear at all — as TWO seed catalogues, which is what D-024 §2 permits:
:data:`ICAAP_SEED_PARAMETERS` (Ghana, pinned by ``202609190055``) and
:data:`ICAAP_JURISDICTION_SEED_PARAMETERS` (Nigeria and Kenya, pinned by
``202609200063``). They are separate because the second repeats the first's
CODES under a different jurisdiction; folding them together would make any
code-keyed comparison — the migration-agreement test is one — silently drop
half the rows.

When a code is not seeded for a tenant's scope the answer is a typed refusal
naming the code, never a default. A deadline the platform invented is worse
than no deadline: the bank would file against it. Scope includes the
JURISDICTION and there is no fallback to another one, which is why publishing a
framework is not the same as making it work.

**The plane (D-078, architecture audit M4).** Every read here is
``record=False``. ``RegulatoryRun.parameter_provenance`` is drained from an
ambient session-scoped ledger at the moment a run row is built, so a recording
read credits whichever run is sealed NEXT in that session with a row it never
consumed. The ICAAP workspace is a REPORT plane: it seals no ``RegulatoryRun``,
it reads sealed ones, and it writes its own governed-row record onto the frozen
snapshot (``icaap/snapshot.py::_parameter_provenance``, D-024). Its reads are
therefore nobody's run provenance.

That was safe until now only because no ICAAP request session happened to seal a
run — an invariant nothing stated and nothing tested, which the obvious next
feature (a "run the IRRBB engine now" button on the block that today can only
say *"no IRRBB run exists for this date"*) breaks in one line. Rather than pin
the invariant, this module removes it: route every ICAAP governed-parameter read
through here and the ledger cannot be reached at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Bank
from app.services import regulatory_parameters

#: How long a report may be filed after the financial year end, in months.
SUBMISSION_MONTHS = "icaap_submission_months"
#: How long after the year end the published disclosure is submitted.
DISCLOSURE_SUBMISSION_MONTHS = "icaap_disclosure_submission_months"
#: Days before the deadline at which readiness turns amber (platform policy).
DEADLINE_AMBER_DAYS = "icaap_deadline_amber_days"
MATERIALITY_MIN_SCORE = "icaap_materiality_material_min_score"
MATERIALITY_MIN_IMPACT = "icaap_materiality_material_min_impact"
MATERIALITY_RATING_BANDS = "icaap_materiality_rating_bands"
STRESS_HORIZON_YEARS_MIN = "icaap_stress_horizon_years_min"
CAPITAL_PLANNING_HORIZON_YEARS_MIN = "icaap_capital_planning_horizon_years_min"
#: The two review intervals belong to the Pillar 2 catalogue
#: (``regulatory_parameters.ICAAP_P2_SEED_PARAMETERS``, pinned for Ghana by
#: ``202609190056``), not to the eight below. They are named here because the
#: Nigerian and Kenyan checklists quote them and the jurisdiction catalogue in
#: this module is what seeds them for NG and KE. A test pins both strings
#: against ``P2_PARAMETER_CODES``, so a rename there cannot leave these rows
#: pointing at a code nothing reads.
REVIEW_MAX_MONTHS = "icaap_review_max_months"
INDEPENDENT_REVIEW_MAX_MONTHS = "icaap_independent_review_max_months"
#: Two more Pillar 2 codes, for the same reason: the register resolves both for
#: every cycle regardless of framework, so an unseeded jurisdiction refuses at
#: readiness even when every code its checklist quotes resolves.
DIVERSIFICATION_BENEFIT_ALLOWED = "icaap_diversification_benefit_allowed"
PILLAR2_SOURCE_TOLERANCE_PCT = "icaap_pillar2_source_tolerance_pct"


@dataclass(frozen=True)
class IcaapParamSeed:
    """One pinned seed row. Mirrored by the migration, which owns the insert."""

    param_code: str
    value: str | None
    value_json: dict[str, Any] | None
    unit: str
    confirmation_status: str
    source_citation: str
    #: Which regulator's rule this row states. Trailing with a default because
    #: every row written before a second jurisdiction was published is Ghanaian
    #: and ``seed_rows`` wrote the literal itself; a test proves each of those
    #: rows still emits an identical dict. Resolution is jurisdiction-scoped
    #: with no fallback, so a Nigerian bank never reads a Ghanaian row.
    jurisdiction_code: str = "GH"


_MATERIALITY_BANDS: dict[str, Any] = {
    "schema": "icaap-score-bands-v1",
    "bands": [
        {"key": "low", "label": "Low", "min_score": 1, "max_score": 4},
        {"key": "medium", "label": "Medium", "min_score": 5, "max_score": 9},
        {"key": "high", "label": "High", "min_score": 10, "max_score": 16},
        {"key": "very_high", "label": "Very high", "min_score": 17, "max_score": 25},
    ],
}

#: The catalogue the hermetic fixtures seed and the migration pins. Values live
#: here and nowhere else in the ICAAP code (D-024 §2).
ICAAP_SEED_PARAMETERS: tuple[IcaapParamSeed, ...] = (
    IcaapParamSeed(
        SUBMISSION_MONTHS,
        "3",
        None,
        "months",
        "pending",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶72: the report is "
        "submitted within three months of the financial year end; pending final text",
    ),
    IcaapParamSeed(
        DISCLOSURE_SUBMISSION_MONTHS,
        "3",
        None,
        "months",
        "pending",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶82: the published "
        "results are submitted by 31 March following the year end; pending final text",
    ),
    IcaapParamSeed(
        DEADLINE_AMBER_DAYS,
        "30",
        None,
        "days",
        "pending",
        "AequorOS platform policy: days before the filing deadline from which ICAAP "
        "readiness shows amber; not a regulatory value",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_SCORE,
        "10",
        None,
        "score",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix: a risk is material at "
        "or above this likelihood x impact score; the BoG ICAAP Guideline prescribes no matrix",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_IMPACT,
        "4",
        None,
        "score",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix: a risk is material at "
        "or above this impact score whatever its likelihood; the BoG ICAAP Guideline "
        "prescribes no matrix",
    ),
    IcaapParamSeed(
        MATERIALITY_RATING_BANDS,
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix rating bands over "
        "likelihood x impact scores; the BoG ICAAP Guideline prescribes no matrix",
    ),
    IcaapParamSeed(
        STRESS_HORIZON_YEARS_MIN,
        "3",
        None,
        "years",
        "pending",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶68, ¶75: pre- and "
        "post-stress capital projected over at least three years; pending final text",
    ),
    IcaapParamSeed(
        CAPITAL_PLANNING_HORIZON_YEARS_MIN,
        "3",
        None,
        "years",
        "pending",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶68, ¶75 and "
        "Appendix II Table 5: capital projected over at least three years; pending final text",
    ),
)

ICAAP_PARAM_CODES: frozenset[str] = frozenset(seed.param_code for seed in ICAAP_SEED_PARAMETERS)


# --- Nigeria and Kenya ------------------------------------------------------
#
# The CBN and CBK frameworks ship as data (D-076) and quote the SAME codes as
# Ghana — but resolution is jurisdiction-scoped with no fallback, so until a row
# exists for NG and for KE every read is a ``missing_parameter`` refusal and
# neither framework can open a cycle. These are those rows.
#
# **What they rest on, stated once here and again in every citation.** Neither
# primary text is in this repository and nobody on this platform has read
# either one. The values come from the extraction record behind
# ``app/domain/icaap/frameworks/{ng,ke}/SOURCES.md`` — a whole-document
# ``pdftotext`` reading made in the P5 design session of 2026-09-19, whose
# ``sha256`` identifies a copy to obtain and CANNOT be re-verified from a
# checkout. That is second-hand provenance, not verified provenance, so every
# row below ships ``confirmation_status='pending'`` and says in its own citation
# that the regulator's text was not read here. The citations deliberately do NOT
# use Ghana's "awaiting stakeholder confirmation" wording, which reads as *the
# regulator has not confirmed our reading* when the true state is *we have not
# read the regulator*.
#
# **What is a platform judgement rather than a regulator's number** carries the
# ``REPRESENTATIVE:`` prefix, which is load-bearing: ``params.ParameterRow``
# tests ``source_citation.startswith(REPRESENTATIVE_PREFIX)``, and that flag is
# what puts the label on readiness, the Pillar 2 register and the frozen
# snapshot's provenance. Moving the prefix off the front of the string silently
# removes the label from every one of those surfaces.
#
# **Not seeded, deliberately** — each is named with its reason in
# ``.ai/icaap/agent-reports/GAP-2.md``: the two "ideally five years" horizon
# codes (no framework quotes them; reinstating the wording is a new framework
# version, not a row), the disclosure period (neither instrument requires
# publication, so both declare ``disclosure: null``), and every SF, granularity,
# CCR and capital-floor calibration (no CBN/CBK figure has been read for any of
# them — those methods must keep refusing rather than borrow Ghana's numbers).

#: The common tail of a Nigerian citation: what the value rests on, and what it
#: does not rest on. Kept as one constant so no row can quietly claim more.
_NG_UNREAD = (
    "Per the 2026-09-19 extraction (ng/SOURCES.md); the CBN text is not held or read "
    "here. Pending verification."
)
_KE_UNREAD = (
    "Per the 2026-09-19 extraction (ke/SOURCES.md); the CBK text is not held or read "
    "here. Pending verification."
)
#: The platform's own 5x5 matrix, which both regulators leave to the bank. Same
#: value as the Ghana row (a test holds them equal); only the sentence naming
#: the instrument differs.
_MATRIX_SCORE = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix; a risk is material at or "
    "above this likelihood x impact score. "
)
_MATRIX_IMPACT = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix; a risk is material at or "
    "above this impact score whatever its likelihood. "
)
_MATRIX_BANDS = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix rating bands over likelihood "
    "x impact scores. "
)
_NG_NO_MATRIX = "The CBN Guideline prescribes no matrix (2026-09-19 extraction, not re-read)."
_KE_NO_MATRIX = "The CBK Guidance Note prescribes no matrix (2026-09-19 extraction, not re-read)."
#: Platform display policy, not a regulatory value — the same sentence Ghana's
#: row carries, because the window is not a regulator's and never was.
_AMBER = (
    "AequorOS platform policy: days before the filing deadline from which ICAAP readiness "
    "shows amber; not a regulatory value"
)

#: Platform policy the Pillar 2 REGISTER resolves for every cycle, whatever the
#: framework (``services/icaap/pillar2.REGISTER_CODES``). Neither is a
#: regulator's number in any jurisdiction, and without them readiness refuses
#: before it reaches a single finding — so a framework without them is not
#: functional even though every code it quotes resolves.
_NO_DIVERSIFICATION = (
    "AequorOS platform policy: Pillar 2 risks are summed with no inter-risk diversification "
    "benefit, the conservative default. No allowance for one has been read for this regulator."
)
_SOURCE_TOLERANCE = (
    "REPRESENTATIVE: AequorOS internal-control tolerance for Pillar 2 source consistency; "
    "relative difference; no published value"
)

#: The Nigerian and Kenyan rows, pinned identically in
#: ``alembic/versions/202609200063_icaap_jurisdiction_parameters.py``. Nothing
#: here is a NEW code: six come from :data:`ICAAP_PARAM_CODES` and two are the
#: Pillar 2 review intervals. What is new is the jurisdiction, which is the
#: whole of the gap — resolution matches on it exactly and never falls back.
ICAAP_JURISDICTION_SEED_PARAMETERS: tuple[IcaapParamSeed, ...] = (
    # --- Nigeria (CBN Revised SRP/ICAAP Guidelines, September 2021) ---------
    IcaapParamSeed(
        SUBMISSION_MONTHS,
        "4",
        None,
        "months",
        "pending",
        f"CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 51: annual report submitted by "
        f"end of April. {_NG_UNREAD}",
        "NG",
    ),
    IcaapParamSeed(
        REVIEW_MAX_MONTHS,
        "12",
        None,
        "months",
        "pending",
        f"CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 52: the ICAAP is updated at least "
        f"annually. {_NG_UNREAD}",
        "NG",
    ),
    IcaapParamSeed(
        INDEPENDENT_REVIEW_MAX_MONTHS,
        "12",
        None,
        "months",
        "pending",
        f"CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 47: independent review and audit, "
        f"frequency stated as varying; read as annual. {_NG_UNREAD}",
        "NG",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_SCORE,
        "10",
        None,
        "score",
        "pending",
        f"{_MATRIX_SCORE}{_NG_NO_MATRIX}",
        "NG",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_IMPACT,
        "4",
        None,
        "score",
        "pending",
        f"{_MATRIX_IMPACT}{_NG_NO_MATRIX}",
        "NG",
    ),
    IcaapParamSeed(
        MATERIALITY_RATING_BANDS,
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        f"{_MATRIX_BANDS}{_NG_NO_MATRIX}",
        "NG",
    ),
    IcaapParamSeed(DEADLINE_AMBER_DAYS, "30", None, "days", "pending", _AMBER, "NG"),
    IcaapParamSeed(
        DIVERSIFICATION_BENEFIT_ALLOWED, "0", None, "boolean", "pending", _NO_DIVERSIFICATION, "NG"
    ),
    IcaapParamSeed(
        PILLAR2_SOURCE_TOLERANCE_PCT, "1", None, "percent", "pending", _SOURCE_TOLERANCE, "NG"
    ),
    # --- Kenya (CBK Guidance Note on ICAAP, November 2016) ------------------
    IcaapParamSeed(
        SUBMISSION_MONTHS,
        "4",
        None,
        "months",
        "pending",
        f"CBK Guidance Note on ICAAP (Nov 2016) para 5(b): not later than 30 April for the "
        f"31 December position, month-end clamped. {_KE_UNREAD}",
        "KE",
    ),
    IcaapParamSeed(
        REVIEW_MAX_MONTHS,
        "12",
        None,
        "months",
        "pending",
        f"CBK Guidance Note on ICAAP (Nov 2016) para 4(i)(c): Board review of the ICAAP "
        f"policies, read as at least annual. {_KE_UNREAD}",
        "KE",
    ),
    IcaapParamSeed(
        INDEPENDENT_REVIEW_MAX_MONTHS,
        "12",
        None,
        "months",
        "pending",
        f"CBK Guidance Note on ICAAP (Nov 2016) para 4(vi)(c): independent review and audit, "
        f"frequency stated as varying; read as annual. {_KE_UNREAD}",
        "KE",
    ),
    IcaapParamSeed(
        STRESS_HORIZON_YEARS_MIN,
        "3",
        None,
        "years",
        "pending",
        f"CBK Guidance Note on ICAAP (Nov 2016) para 4(iv)(a): forward-looking stress tests "
        f"over a minimum of three years. {_KE_UNREAD}",
        "KE",
    ),
    IcaapParamSeed(
        CAPITAL_PLANNING_HORIZON_YEARS_MIN,
        "3",
        None,
        "years",
        "pending",
        f"CBK Guidance Note on ICAAP (Nov 2016) para 2(b): capital forecast over a minimum of "
        f"three years. {_KE_UNREAD}",
        "KE",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_SCORE,
        "10",
        None,
        "score",
        "pending",
        f"{_MATRIX_SCORE}{_KE_NO_MATRIX}",
        "KE",
    ),
    IcaapParamSeed(
        MATERIALITY_MIN_IMPACT,
        "4",
        None,
        "score",
        "pending",
        f"{_MATRIX_IMPACT}{_KE_NO_MATRIX}",
        "KE",
    ),
    IcaapParamSeed(
        MATERIALITY_RATING_BANDS,
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        f"{_MATRIX_BANDS}{_KE_NO_MATRIX}",
        "KE",
    ),
    IcaapParamSeed(DEADLINE_AMBER_DAYS, "30", None, "days", "pending", _AMBER, "KE"),
    IcaapParamSeed(
        DIVERSIFICATION_BENEFIT_ALLOWED, "0", None, "boolean", "pending", _NO_DIVERSIFICATION, "KE"
    ),
    IcaapParamSeed(
        PILLAR2_SOURCE_TOLERANCE_PCT, "1", None, "percent", "pending", _SOURCE_TOLERANCE, "KE"
    ),
)

#: The jurisdictions the catalogue above covers, for the fixtures and the tests.
ICAAP_SEEDED_JURISDICTIONS: frozenset[str] = frozenset(
    seed.jurisdiction_code for seed in ICAAP_JURISDICTION_SEED_PARAMETERS
)


def _row(seed: IcaapParamSeed, actor: str) -> dict[str, object]:
    return {
        "scope_type": "institution_class",
        "scope_key": "bank",
        "param_code": seed.param_code,
        "jurisdiction_code": seed.jurisdiction_code,
        "value_numeric": None if seed.value is None else Decimal(seed.value),
        "value_json": seed.value_json,
        "unit": seed.unit,
        "source_citation": seed.source_citation,
        "confirmation_status": seed.confirmation_status,
        "effective_from": regulatory_parameters.SEED_EFFECTIVE_FROM,
        "effective_to": None,
        "status": "approved",
        "proposed_by": actor,
        "approved_by": actor,
    }


def seed_rows(actor: str = regulatory_parameters.SEED_ACTOR) -> list[dict[str, object]]:
    """The Ghana catalogue as insertable rows, for a ``create_all`` database.

    The hermetic pytest suite and the Playwright stack run no migration, so the
    shared reference-data fixture inserts these. Same shape as
    ``regulatory_parameters.seed_rows`` so neither can drift from the other.
    """
    return [_row(seed, actor) for seed in ICAAP_SEED_PARAMETERS]


def jurisdiction_seed_rows(
    actor: str = regulatory_parameters.SEED_ACTOR,
) -> list[dict[str, object]]:
    """Just the Nigerian and Kenyan rows, for a database that has the rest.

    A separate function for the same reason ``regulatory_parameters.p2_seed_rows``
    is separate: the bulk seeds fire under a "the table is empty" guard, which is
    right for a database built before these rows existed and wrong for one built
    after. It is also a separate CATALOGUE from :data:`ICAAP_SEED_PARAMETERS`
    rather than more entries in it, because these rows repeat its codes under a
    different jurisdiction — folding them in would make any code-keyed comparison
    (the migration-agreement test is one) silently drop half of them.
    """
    return [_row(seed, actor) for seed in ICAAP_JURISDICTION_SEED_PARAMETERS]


def missing_parameter(param_code: str, detail: str) -> HTTPException:
    """The one refusal shape for an unseeded ICAAP parameter."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "missing_parameter",
            "param_code": param_code,
            "message": (
                f"{detail} It is a governed parameter ({param_code}) with no approved row for "
                "this institution — staff configure it in the operator console."
            ),
        },
    )


def try_resolve(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> regulatory_parameters.ResolvedParameter | None:
    """An optional ICAAP parameter, or ``None`` when no approved row exists.

    The ICAAP report plane's ONE door to a governed row — non-recording, for the
    reason in the module docstring. Nothing under ``app/services/icaap`` calls
    ``regulatory_parameters.try_resolve``/``resolve`` directly; a guard in
    ``tests/architecture/test_icaap_boundaries.py`` keeps it that way, so the
    plane boundary cannot be reopened by a call site that did not know it existed.
    """
    return regulatory_parameters.try_resolve(db, bank, param_code, as_of=as_of, record=False)


def resolve(
    db: Session, bank: Bank, param_code: str, *, purpose: str, as_of: date | None = None
) -> regulatory_parameters.ResolvedParameter:
    """A mandatory ICAAP parameter, or a typed ``missing_parameter`` refusal."""
    try:
        return regulatory_parameters.resolve(db, bank, param_code, as_of=as_of, record=False)
    except regulatory_parameters.RegulatoryParameterError as exc:
        raise missing_parameter(param_code, purpose) from exc


def try_resolve_int(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> int | None:
    """A whole-number parameter, or None when none is configured.

    For values a deployment may legitimately not have set — a display window,
    not a regulatory floor. The caller must say so in its output rather than
    substitute anything (D-036).
    """
    resolved = try_resolve(db, bank, param_code, as_of=as_of)
    if resolved is None or resolved.value is None:
        return None
    if resolved.value != resolved.value.to_integral_value():
        return None
    return int(resolved.value)


def resolve_int(
    db: Session, bank: Bank, param_code: str, *, purpose: str, as_of: date | None = None
) -> int:
    """A whole-number parameter (months, days, years)."""
    resolved = resolve(db, bank, param_code, purpose=purpose, as_of=as_of)
    value = resolved.value
    if value is None or value != value.to_integral_value():
        raise missing_parameter(param_code, f"{purpose} It must be a whole number.")
    return int(value)


__all__ = [
    "CAPITAL_PLANNING_HORIZON_YEARS_MIN",
    "DEADLINE_AMBER_DAYS",
    "DISCLOSURE_SUBMISSION_MONTHS",
    "DIVERSIFICATION_BENEFIT_ALLOWED",
    "ICAAP_JURISDICTION_SEED_PARAMETERS",
    "ICAAP_PARAM_CODES",
    "ICAAP_SEEDED_JURISDICTIONS",
    "ICAAP_SEED_PARAMETERS",
    "INDEPENDENT_REVIEW_MAX_MONTHS",
    "MATERIALITY_MIN_IMPACT",
    "MATERIALITY_MIN_SCORE",
    "MATERIALITY_RATING_BANDS",
    "PILLAR2_SOURCE_TOLERANCE_PCT",
    "REVIEW_MAX_MONTHS",
    "STRESS_HORIZON_YEARS_MIN",
    "SUBMISSION_MONTHS",
    "IcaapParamSeed",
    "jurisdiction_seed_rows",
    "missing_parameter",
    "resolve",
    "resolve_int",
    "try_resolve",
    "try_resolve_int",
    "seed_rows",
]
