"""SDI simplified s.29 capital adequacy — the LIVE CAR the s.29 view shows.

Computed directly from canonical data (no Basel live-engine dependency, which is
why the bank capital dashboard cannot serve an SDI): Net Own Funds ÷ Risk-Weighted
Assets against the Act 930 s.29 floor. Additive to ``sdi_capital_checks`` (paid-up
+ statutory-reserve).

Fail-closed discipline (forensic audit 2026-08-21 §10, "SDI / universal boundary")
---------------------------------------------------------------------------------
Every regulatory number comes from the control plane and **nothing is substituted
when a link does not resolve**:

* the s.29 floor (``car_min``) must resolve to a scalar. It used to read
  ``car_min_param.normalized_value or _ZERO`` — a row with a null value therefore
  produced a 0% floor, against which every CAR is "green". That is now
  :class:`SdiCapitalPolicyUnresolved`.
* each simplified bucket weight (``risk_weight_<bucket>``) must resolve. A missing
  weight used to become 100% silently while the docstring claimed nothing was
  hardcoded. A weight that cannot be established now refuses the whole ratio: a
  total that is missing a component is not a total.
* a position in a currency other than the institution's reporting currency with no
  ingested ``balance_ghs`` conversion used to be counted **at face value as GHS**.
  It is now excluded (never a made-up rate — the same rule
  ``regulatory_liquidity._currency_ladders`` applies) and reported so the exclusion
  blocks filing instead of quietly understating RWA.

The position-type → bucket taxonomy is governed policy data
(``sdi_rwa_bucket_map``, resolved through the control plane). When no governed row
exists the documented code default is used and the summary carries
``bucket_map_source='code_default'``, which marks the CAR provisional and blocks
filing (:func:`assert_bucket_map_filable`, called on the official mint) — the
taxonomy is never presented as confirmed when it is not.

Which RISK CLASSES the ratio covers (forensic audit 2026-08-21, "DIVERGENCE #1")
--------------------------------------------------------------------------------
The bank engine charges credit, market and operational risk. This one charged
credit risk alone, and said so nowhere: an SDI's CAR silently omitted two risk
classes while presenting itself as *the* capital adequacy ratio.

Whether s.29 risk-weighted assets should carry market and operational charges
cannot be settled from this repository — the Capital Requirements Directive
excludes specialised deposit-taking institutions by its own ¶2, so no published
instrument answers it (``docs/bog_parameter_sources.md`` §2.4). What can be fixed
is that the composition was IMPLICIT. It is now declared data, resolved through
the control plane exactly like the bucket map:

* ``sdi_rwa_composition`` (:data:`COMPOSITION_PARAM`) declares, per risk class,
  how it is measured. A governed row is a COMPLETE declaration — a class it does
  not name is out of scope, the same rule the bucket map already applies to a
  position type it does not name.
* Absent a governed row the documented default applies (credit risk only) and the
  summary carries ``composition_source='code_default'``, which marks the ratio
  provisional and blocks filing. "Blocks filing" is a live property, not a
  docstring: :func:`assert_official_rwa_scope_governed` is called on the official
  capital mint (``regulatory_capital.create_capital_run`` /
  ``run_all_capital_scenarios``) and refuses the run before it is created. Until
  2026-08-22 that sentence was true only of an advisory read model, so an SDI with
  no governed row minted a sealed, filable CAR on the code default (audit D-19).
* Every known risk class appears on :attr:`SdiCapitalSummary.risk_classes` whether
  or not it is in scope, each with the reason it contributes what it contributes.
  An omission a reader cannot see is the defect; an omission stated on the face of
  the ratio is a disclosed scope.
* Nothing is invented. No market or operational percentage is defaulted or
  borrowed from Basel. A class brought into scope by a governed row whose
  measurement needs a percentage must have that percentage in the control plane
  too, or the whole ratio refuses (:class:`SdiCapitalPolicyUnresolved`) — a total
  missing a declared component is not a total.

ONE authority, not one per path (2026-08-22, WS-X)
--------------------------------------------------
The scope was briefly expressed twice: the governed composition drove this live
view, while ``regulatory_capital._SDI_STRUCTURAL_CAPITAL`` restated it
structurally for the OFFICIAL filing run by zeroing the Basel measurement rates.
A governed row that turned a charge on would have moved the live CAR and left the
immutable filed CAR behind — the same defect one layer down.

:func:`resolve_rwa_scope` is now the single entry point, and all three SDI capital
paths consume the :class:`SdiRwaScope` it returns rather than restating it: this
module (live), ``regulatory_capital._sdi_engine_params`` (the filing run), and
``enterprise_stress._sdi_capital_params`` (the solvency stress projection). A
charged class reaches the shared engine as ``CapitalParams.rwa_pct_of_credit_rwa``
— always empty for a bank, so the BoG CRD path is byte-identical. The gate that
keeps them from re-forking is ``test_sdi_regime_boundary.py`` §2d.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import (
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    outcome,
)
from app.domain.capital.engine import (
    RWA_CLASS_CREDIT,
    RWA_CLASS_MARKET,
    RWA_CLASS_OPERATIONAL,
)
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot, CanonicalReferenceRow
from app.services import institution_types
from app.services import regulatory_parameters as rp
from app.services.jurisdictions import base_currency, regulator_name

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
# Canonical validation statuses that count as usable book data (matches
# fact_derivation / loan_classification; "warning" is accepted-with-warnings).
_INCLUDED = ("accepted", "warning")

# CET1 own-funds deduction tiers (subtract from NOF); everything else adds.
DEDUCTION_TIERS = frozenset({"cet1_deduction", "at1_deduction", "tier2_deduction"})

#: Control-plane parameter carrying the governed position-type → bucket map.
#: A ``value_json`` row of ``{"<POSITION_TYPE>": "<bucket>"}``; the bucket name
#: selects the ``risk_weight_<bucket>`` weight.
BUCKET_MAP_PARAM = "sdi_rwa_bucket_map"

#: ``bucket_map_source`` values.
BUCKET_MAP_CONTROL_PLANE = "control_plane"
BUCKET_MAP_CODE_DEFAULT = "code_default"

#: Control-plane parameter DECLARING the s.29 risk-weighted-asset composition:
#: a ``value_json`` row of ``{"<risk class>": "<measurement>"}``. A governed row
#: is a COMPLETE declaration — a class it does not name is out of scope, and a
#: class mapped to a falsy value (``false``/``null``/``""``) is explicitly out.
COMPOSITION_PARAM = "sdi_rwa_composition"

#: ``composition_source`` values (same vocabulary as ``bucket_map_source``).
COMPOSITION_CONTROL_PLANE = "control_plane"
COMPOSITION_CODE_DEFAULT = "code_default"

#: The risk classes an s.29 total could cover. Every one of them is reported on
#: the summary, in scope or not, so an omission is never invisible. The names are
#: ALIASES of the capital engine's vocabulary, not a second spelling: the official
#: filing run keys ``CapitalParams.rwa_pct_of_credit_rwa`` on exactly these.
RISK_CLASS_CREDIT = RWA_CLASS_CREDIT
RISK_CLASS_MARKET = RWA_CLASS_MARKET
RISK_CLASS_OPERATIONAL = RWA_CLASS_OPERATIONAL
KNOWN_RISK_CLASSES: tuple[str, ...] = (
    RISK_CLASS_CREDIT,
    RISK_CLASS_MARKET,
    RISK_CLASS_OPERATIONAL,
)

#: Measurements this module implements. A governed composition may only name one
#: of these — an unrecognised measurement refuses the ratio rather than silently
#: contributing zero.
#:
#: ``bucket_weighted_exposure``
#:     Σ on-balance-sheet asset exposure × the simplified ``risk_weight_<bucket>``
#:     weight. The credit measurement, and the only one that reads the book.
#: ``pct_of_credit_rwa``
#:     The class contributes a percentage of credit risk-weighted assets. The
#:     percentage is itself governed (:func:`charge_param_code`) and is never
#:     defaulted: this is the seam a prescribed market or operational charge
#:     plugs into with two control-plane rows and no code change.
MEASURE_BUCKET_WEIGHTED_EXPOSURE = "bucket_weighted_exposure"
MEASURE_PCT_OF_CREDIT_RWA = "pct_of_credit_rwa"
SUPPORTED_MEASUREMENTS: tuple[str, ...] = (
    MEASURE_BUCKET_WEIGHTED_EXPOSURE,
    MEASURE_PCT_OF_CREDIT_RWA,
)


def charge_param_code(risk_class: str) -> str:
    """The control-plane parameter carrying a ``pct_of_credit_rwa`` percentage."""
    return f"rwa_charge_{risk_class}_pct_of_credit_rwa"


#: The DOCUMENTED DEFAULT composition, not the authority. It matches what the
#: module has always computed — credit risk only — and is reported as
#: ``composition_source='code_default'`` until a governed row replaces it.
#: Market and operational risk are absent because no instrument prescribes a
#: charge for this class, NOT because the charge is zero.
_DEFAULT_COMPOSITION: dict[str, str] = {
    RISK_CLASS_CREDIT: MEASURE_BUCKET_WEIGHTED_EXPOSURE,
}

# On-balance-sheet ASSET position type → the simplified SDI risk-weight bucket
# (control-plane ``risk_weight_<bucket>``). LOANs are the 100% "other loans" bucket;
# a mortgage sub-split (50%, the seeded-but-unreachable ``risk_weight_mortgage``)
# needs a product-taxonomy key on the governed map and a BoG-confirmed boundary.
# Only assets bear risk weight — any position type NOT in this map (deposits,
# borrowings, derivatives, off-balance LCs/commitments, equity) is skipped, never
# swept into a 100% bucket: a liability is never risk-weighted.
#
# This is the DOCUMENTED DEFAULT, not the authority: a governed
# ``sdi_rwa_bucket_map`` row overrides it, and while none exists every summary
# reports ``bucket_map_source='code_default'``.
_BUCKET_BY_TYPE: dict[str, str] = {
    "CASH": "cash",
    "SECURITY_HOLDING": "sovereign",
    "INTERBANK_PLACEMENT": "interbank",
    "LOAN": "other_loans",
    "OTHER_ASSET": "other_assets",
}


class SdiCapitalPolicyUnresolved(HTTPException, NotComputable):
    """A governed input of the s.29 ratio does not resolve — nothing is substituted.

    Doubly typed exactly like ``institution_types.InstitutionTypeUnresolved``:
    ``HTTPException`` (409, the codebase's configured-state conflict code) so an
    API caller gets a precise, actionable message instead of a 500, and
    ``NotComputable`` so a boundary that already handles WS-A fail-closed
    outcomes handles this one identically.
    """

    def __init__(self, detail: OutcomeDetail) -> None:
        NotComputable.__init__(self, detail)
        HTTPException.__init__(self, status_code=status.HTTP_409_CONFLICT, detail=detail.message)


def _policy_unresolved(
    bank: Bank, as_of: date, metric_id: str, codes: list[str], reason: str
) -> SdiCapitalPolicyUnresolved:
    return SdiCapitalPolicyUnresolved(
        outcome(
            OutcomeState.POLICY_UNRESOLVED,
            metric_id=metric_id,
            reason=reason,
            items=tuple(f"param:{code}" for code in codes),
            context={
                "bank_id": bank.id,
                "organization_id": bank.organization_id,
                "as_of": as_of.isoformat(),
                "param_codes": list(codes),
            },
        )
    )


@dataclass(frozen=True)
class RiskWeightBand:
    bucket: str
    weight_pct: Decimal
    exposure_ghs: Decimal
    rwa_ghs: Decimal
    confirmation_status: str


def _readable(names: tuple[str, ...]) -> str:
    """Join names the way a sentence does: a / a and b / a, b and c."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


@dataclass(frozen=True)
class RwaRiskClass:
    """One risk class of the declared s.29 RWA composition, in scope or not.

    Out-of-scope classes are carried too, and that is the point: a capital ratio
    that charges for credit risk alone has to SAY so on the surface that presents
    it, not only in the code that computes it.
    """

    risk_class: str
    in_scope: bool
    #: The declared measurement, or ``None`` when the class is out of scope.
    measurement: str | None
    rwa_ghs: Decimal
    #: Production copy: what this class contributes and why. Shown to a reader.
    note: str


@dataclass(frozen=True)
class SdiCapitalSummary:
    as_of: date
    net_own_funds_ghs: Decimal
    total_rwa_ghs: Decimal
    car_pct: Decimal | None
    car_min_pct: Decimal
    status: str  # green | red | na
    car_min_confirmation: str
    computable: bool
    bands: list[RiskWeightBand] = field(default_factory=list)
    pending_parameters: list[str] = field(default_factory=list)
    #: 'control_plane' when a governed ``sdi_rwa_bucket_map`` row supplied the
    #: position-type → bucket taxonomy, 'code_default' while none exists.
    bucket_map_source: str = BUCKET_MAP_CODE_DEFAULT
    #: Asset positions excluded from RWA because they are in a currency other
    #: than the institution's reporting currency and carry no ingested
    #: conversion. Excluding them UNDERSTATES RWA, so they block filing.
    unconverted_position_count: int = 0
    #: The currencies those positions are denominated in, for the message.
    unconverted_currencies: tuple[str, ...] = ()
    #: 'control_plane' when a governed ``sdi_rwa_composition`` row declared which
    #: risk classes the ratio covers, 'code_default' while none exists.
    composition_source: str = COMPOSITION_CODE_DEFAULT
    #: Every known risk class, in scope or not, with what it contributed and why.
    risk_classes: list[RwaRiskClass] = field(default_factory=list)

    @property
    def taxonomy_confirmed(self) -> bool:
        return self.bucket_map_source == BUCKET_MAP_CONTROL_PLANE

    @property
    def composition_confirmed(self) -> bool:
        """Has a governed row declared which risk classes the ratio covers?"""
        return self.composition_source == COMPOSITION_CONTROL_PLANE

    @property
    def included_risk_classes(self) -> tuple[str, ...]:
        return tuple(row.risk_class for row in self.risk_classes if row.in_scope)

    @property
    def excluded_risk_classes(self) -> tuple[str, ...]:
        """Known risk classes the ratio does NOT charge for. Never empty today."""
        return tuple(row.risk_class for row in self.risk_classes if not row.in_scope)

    @property
    def rwa_scope_note(self) -> str:
        """One sentence stating what this ratio covers — and what it leaves out.

        This is the disclosure the pre-fix summary had no way to make. It is
        production copy: a reader sees the scope of the number they are looking
        at, not a parameter code.
        """
        included = _readable(self.included_risk_classes)
        excluded = _readable(self.excluded_risk_classes)
        if not included:
            return (
                "Risk-weighted assets charge for no risk class at all, so this "
                "ratio measures nothing until a scope is configured."
            )
        if not excluded:
            return f"Risk-weighted assets charge for {included} risk."
        return (
            f"Risk-weighted assets charge for {included} risk only. No {excluded} "
            "risk charge is applied, so this is a narrower measure than a bank's "
            "capital adequacy ratio and must be read as one."
        )


def _dec(value: object) -> Decimal:
    if value is None or value == "":
        return _ZERO
    return Decimal(str(value))


def signed_component_amount(payload: Mapping[str, object]) -> Decimal:
    """The SIGNED ``capital_structure`` amount of one ingested row.

    A negative amount already encodes a deduction; a deduction tier flips a
    positive magnitude. Guards against double-negation. THE one signing rule for
    the dataset — ``sdi_capital_checks.capital_components`` imports it rather
    than keeping its own (it used ``abs()``, so a deduction row INCREASED the
    paid-up and statutory-reserve totals it fed, while this module summed the
    same rows signed; forensic audit 2026-08-21).
    """
    amount = _dec(payload.get("amount_ghs"))
    tier = str(payload.get("tier", "")).strip().lower()
    return -abs(amount) if tier in DEDUCTION_TIERS else amount


def latest_capital_structure_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[CanonicalReferenceRow]:
    """The latest ingested ``capital_structure`` generation on/before ``as_of``
    (latest as_of_date, then latest batch — the reference-dataset supersession
    convention). Empty when the dataset was never ingested."""
    scope = (
        CanonicalReferenceRow.organization_id == ctx.organization_id,
        CanonicalReferenceRow.bank_id == bank.id,
        CanonicalReferenceRow.dataset_kind == "capital_structure",
    )
    latest = db.scalar(
        select(func.max(CanonicalReferenceRow.as_of_date)).where(
            *scope, CanonicalReferenceRow.as_of_date <= as_of
        )
    )
    if latest is None:
        return []
    batch = db.scalar(
        select(CanonicalReferenceRow.ingestion_batch_id)
        .where(*scope, CanonicalReferenceRow.as_of_date == latest)
        .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
        .limit(1)
    )
    return list(
        db.scalars(
            select(CanonicalReferenceRow).where(
                *scope,
                CanonicalReferenceRow.as_of_date == latest,
                CanonicalReferenceRow.ingestion_batch_id == batch,
            )
        )
    )


def _net_own_funds(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> Decimal:
    """Signed sum of the ingested ``capital_structure`` components — deduction
    tiers subtract, everything else adds — i.e. Net Own Funds."""
    return sum(
        (
            signed_component_amount(row.payload or {})
            for row in latest_capital_structure_rows(db, ctx, bank, as_of)
        ),
        _ZERO,
    )


def net_own_funds(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> Decimal:
    """The signed Act 930 s.29 Net Own Funds denominator for SDI consumers."""
    return _net_own_funds(db, ctx, bank, as_of)


def prefetch_net_own_funds(
    db: Session, ctx: TenantContext, bank: Bank, as_of_dates: list[date]
) -> dict[date, Decimal]:
    """Resolve request-scoped SDI Net Own Funds without per-date queries."""
    dates = sorted(set(as_of_dates))
    if not dates:
        return {}
    scope = (
        CanonicalReferenceRow.organization_id == ctx.organization_id,
        CanonicalReferenceRow.bank_id == bank.id,
        CanonicalReferenceRow.dataset_kind == "capital_structure",
    )
    generation_queries = []
    for as_of in dates:
        winner = (
            select(
                CanonicalReferenceRow.as_of_date.label("as_of_date"),
                CanonicalReferenceRow.ingestion_batch_id.label("ingestion_batch_id"),
            )
            .where(*scope, CanonicalReferenceRow.as_of_date <= as_of)
            .order_by(
                CanonicalReferenceRow.as_of_date.desc(),
                CanonicalReferenceRow.created_at.desc(),
                CanonicalReferenceRow.id.desc(),
            )
            .limit(1)
            .subquery()
        )
        generation_queries.append(
            select(
                literal(as_of).label("requested_date"),
                winner.c.as_of_date,
                winner.c.ingestion_batch_id,
            )
        )
    generations = list(db.execute(union_all(*generation_queries)).all())
    selected_generations = {
        (generation_date, batch_id) for _, generation_date, batch_id in generations
    }
    if not selected_generations:
        return dict.fromkeys(dates, _ZERO)

    generation_scope = or_(
        *(
            and_(
                CanonicalReferenceRow.as_of_date == generation_date,
                CanonicalReferenceRow.ingestion_batch_id == batch_id,
            )
            for generation_date, batch_id in selected_generations
        )
    )
    rows = list(
        db.scalars(
            select(CanonicalReferenceRow).where(
                *scope,
                generation_scope,
            )
        )
    )
    totals_by_generation: dict[tuple[date, object], Decimal] = {}
    for row in rows:
        key = (row.as_of_date, row.ingestion_batch_id)
        totals_by_generation[key] = totals_by_generation.get(key, _ZERO) + signed_component_amount(
            row.payload or {}
        )

    generation_by_date: dict[date, tuple[date, object]] = {}
    for as_of, generation_date, batch_id in generations:
        generation_by_date[as_of] = (generation_date, batch_id)
    return {
        as_of: totals_by_generation.get(generation_by_date[as_of], _ZERO)
        if as_of in generation_by_date
        else _ZERO
        for as_of in dates
    }


def resolve_bucket_map(db: Session, bank: Bank, as_of: date) -> tuple[dict[str, str], str]:
    """The governed position-type → risk-weight-bucket map and its source.

    Governed policy data first (``sdi_rwa_bucket_map`` in the control plane); the
    documented code default otherwise, flagged as such so no surface can present
    an unconfirmed taxonomy as confirmed.
    """
    resolved = rp.try_resolve(db, bank, BUCKET_MAP_PARAM, as_of=as_of)
    payload = resolved.value_json if resolved is not None else None
    if isinstance(payload, Mapping):
        governed = {
            str(key).strip().upper(): str(value).strip().lower()
            for key, value in payload.items()
            if str(key).strip() and str(value).strip()
        }
        if governed:
            return governed, BUCKET_MAP_CONTROL_PLANE
    return dict(_BUCKET_BY_TYPE), BUCKET_MAP_CODE_DEFAULT


#: Values a governed composition may use to put a class explicitly out of scope.
_EXPLICITLY_OUT = ("", "false", "none", "null", "excluded", "not_applicable", "0")


def _resolve_composition_row(
    db: Session,
    bank: Bank,
    as_of: date,
    *,
    resolver: rp.PrefetchedParameterResolver | None = None,
) -> tuple[dict[str, str], str, str | None]:
    """``(composition, source, confirmation_status)`` from the control plane.

    The third element is the governed row's own ``confirmation_status`` —
    ``None`` while the scope is the documented code default. It exists because
    "a governed row declared this" and "the declaration is confirmed against a
    published instrument" are different questions, and the official filing gate
    (:func:`assert_scope_filable`) has to ask both.
    """
    resolved = (
        resolver.try_resolve(COMPOSITION_PARAM, as_of=as_of)
        if resolver is not None
        else rp.try_resolve(db, bank, COMPOSITION_PARAM, as_of=as_of)
    )
    if resolved is not None and isinstance(resolved.value_json, Mapping):
        governed: dict[str, str] = {}
        for key, value in resolved.value_json.items():
            risk_class = str(key).strip().lower()
            measurement = ("" if value is None else str(value)).strip().lower()
            if not risk_class or measurement in _EXPLICITLY_OUT:
                continue
            governed[risk_class] = measurement
        if governed:
            return governed, COMPOSITION_CONTROL_PLANE, resolved.confirmation_status
    return dict(_DEFAULT_COMPOSITION), COMPOSITION_CODE_DEFAULT, None


# NOTE (audit 2026-08-22 D-19/D-20). A public ``resolve_rwa_composition`` used to
# sit here, returning the composition and its source WITHOUT resolving the charges,
# the pending parameters or the confirmation status. It had no caller, and adopting
# it would have re-created the defect D-19 closed: a second answer to "what does an
# SDI charge for?", this one unable to refuse. :func:`resolve_rwa_scope` below is
# the one entry point; a caller that only wants the composition reads
# ``SdiRwaScope.composition`` off the object it already has.


@dataclass(frozen=True)
class SdiRwaScope:
    """THE resolved answer to "what does an SDI's risk-weighted assets charge for?".

    ONE authority for EVERY SDI capital path — the live s.29 view computed in this
    module, the immutable filing run assembled by
    ``regulatory_capital._sdi_engine_params``, and the solvency stress projection
    in ``enterprise_stress._sdi_capital_params``. The official path used to restate
    the same scope structurally (zeroed ``fx_charge_pct``/``bia_alpha_pct``), so a
    governed row that turned a charge on would have moved the live CAR and left the
    official CAR behind. It now consumes this object instead, and the two cannot
    diverge without diverging here first.

    Everything on it is already RESOLVED: no caller re-reads the control plane, and
    a percentage that would not resolve raised :class:`SdiCapitalPolicyUnresolved`
    before this object existed — a scope you hold is a scope that is complete.
    """

    #: risk class -> declared measurement. A class absent from it is out of scope.
    composition: Mapping[str, str]
    #: 'control_plane' when a governed row declared this, 'code_default' otherwise.
    source: str
    #: risk class -> governed percentage of credit RWA, for the classes measured
    #: that way. Never defaulted, never borrowed from Basel, never a zero standing
    #: in for an unresolved row.
    pct_of_credit_rwa: Mapping[str, Decimal]
    #: Governed rows consumed here that are still pending regulator confirmation.
    pending_parameters: tuple[str, ...] = ()
    #: The governed composition row's own ``confirmation_status`` ('confirmed' |
    #: 'pending'); ``None`` while the scope is the documented code default.
    confirmation_status: str | None = None

    @property
    def confirmed(self) -> bool:
        """Did a governed row declare this scope, or is it the code default?"""
        return self.source == COMPOSITION_CONTROL_PLANE

    @property
    def filable(self) -> bool:
        """May an OFFICIAL, sealed, filable run be minted against this scope?

        Three conditions, all governance rather than arithmetic (audit
        2026-08-22 D-19): a governed row must have DECLARED the scope (the code
        default is a documented placeholder, not a determination); that row must
        be CONFIRMED against a published instrument rather than pending; and no
        charge it brings into scope may still be pending. Anything less is an
        unresolved policy, and an unresolved policy must not become a filed CAR.

        The live/indicative view does not consult this — it computes and labels
        (``SdiCapitalSummary.rwa_scope_note``, ``composition_source``). The
        distinction is deliberate: a bank officer may look at a provisional
        ratio; a regulator may not be handed one as a filing.
        """
        return (
            self.source == COMPOSITION_CONTROL_PLANE
            and self.confirmation_status == "confirmed"
            and not self.pending_parameters
        )

    @property
    def credit_in_scope(self) -> bool:
        return self.composition.get(RISK_CLASS_CREDIT) == MEASURE_BUCKET_WEIGHTED_EXPOSURE

    def total_rwa_from_credit(self, credit_rwa: Decimal) -> Decimal:
        """Total risk-weighted assets this scope implies for a given credit RWA.

        The closed form of the composition, and the reason the two paths agree by
        construction: whatever measures credit risk — the simplified bucket table
        on the live view, the risk-weight register on the filing run — the classes
        charged on top of it, and their percentages, come from here.
        """
        total = credit_rwa if self.credit_in_scope else _ZERO
        for pct in self.pct_of_credit_rwa.values():
            total += credit_rwa * pct / _HUNDRED
        return total


def default_rwa_scope() -> SdiRwaScope:
    """The DOCUMENTED DEFAULT scope — credit risk only — with no database read.

    The single definition of "what an SDI charges for when nothing is governed",
    so a caller that has no session still gets the same answer as one that does.
    """
    return SdiRwaScope(
        composition=dict(_DEFAULT_COMPOSITION),
        source=COMPOSITION_CODE_DEFAULT,
        pct_of_credit_rwa={},
    )


def resolve_rwa_scope(
    db: Session,
    bank: Bank,
    as_of: date,
    *,
    resolver: rp.PrefetchedParameterResolver | None = None,
) -> SdiRwaScope:
    """Resolve the governed RWA scope, percentages and all — the ONE entry point.

    Every SDI capital path calls this and none restates it. Refuses rather than
    substituting: a declared class whose charge does not resolve, a class pointed
    at a measurement this platform does not implement, and a class pointed at the
    bucket table that is not credit risk all raise
    :class:`SdiCapitalPolicyUnresolved` — a total missing a declared component is
    not a total.
    """
    composition, source, confirmation = _resolve_composition_row(db, bank, as_of, resolver=resolver)
    charges: dict[str, Decimal] = {}
    pending: list[str] = []
    for risk_class in sorted(composition):
        measurement = composition[risk_class]
        if measurement == MEASURE_BUCKET_WEIGHTED_EXPOSURE:
            if risk_class != RISK_CLASS_CREDIT:
                # The bucket table risk-weights ASSET exposure. It measures credit
                # risk and nothing else; pointing another class at it would relabel
                # the same number, not measure a second risk.
                raise _measurement_unresolved(
                    bank, as_of, risk_class, measurement, COMPOSITION_PARAM
                )
            continue
        if measurement == MEASURE_PCT_OF_CREDIT_RWA and risk_class != RISK_CLASS_CREDIT:
            code = charge_param_code(risk_class)
            resolved = (
                resolver.try_resolve(code, as_of=as_of)
                if resolver is not None
                else rp.try_resolve(db, bank, code, as_of=as_of)
            )
            charge = resolved.normalized_value if resolved is not None else None
            if resolved is None or charge is None:
                raise _policy_unresolved(
                    bank,
                    as_of,
                    "total_rwa_ghs",
                    [COMPOSITION_PARAM, code],
                    f"The capital-adequacy scope declares {risk_class} risk in scope, "
                    f"but the {risk_class}-risk charge ({code}) resolves to no value. "
                    "No percentage is assumed and none is borrowed from the bank "
                    "framework, so the ratio refuses rather than reporting a total "
                    "that is missing a component it says it includes. Set the charge "
                    "in the regulatory-parameter control plane.",
                )
            if resolved.is_pending:
                pending.append(code)
            charges[risk_class] = charge
            continue
        # Credit measured as a percentage of itself is circular; anything else is
        # a measurement this platform does not implement.
        raise _measurement_unresolved(bank, as_of, risk_class, measurement, COMPOSITION_PARAM)
    return SdiRwaScope(
        composition=composition,
        source=source,
        pct_of_credit_rwa=charges,
        pending_parameters=tuple(sorted(set(pending))),
        confirmation_status=confirmation,
    )


def assert_scope_filable(bank: Bank, as_of: date, scope: SdiRwaScope) -> None:
    """Refuse to MINT an immutable capital run on a scope nobody determined.

    Audit 2026-08-22 D-19. Whether an SDI's s.29 risk-weighted assets carry a
    market-risk or an operational-risk charge is a REGULATORY determination, not
    an engineering default, and Act 930 says so in terms. s.29(2) fixes the floor
    ("The minimum capital adequacy ratio shall be at least ten percent") but
    s.29(4) delegates the arithmetic — the ratio "shall be calculated in
    accordance with the methodology prescribed in the directive issued by the
    Bank of Ghana" — and s.29(5) delegates the SCOPE specifically: the Bank of
    Ghana "may, for the purpose of calculating the minimum capital adequacy
    ratio, define eligible capital, CATEGORIES OF RISK ASSETS and appropriate
    adjustments and additions". s.29(3)(b) lets it set different ratios for
    different classes of specialised deposit-taking institution.

    That directive exists for banks and not for this class. The Capital
    Requirements Directive 2018 states its own scope at ¶2 — "This framework
    shall apply to banks licensed and operating under the BSDI Act" — and it is
    the instrument carrying both the ¶73(a) three-class composition (credit +
    market + operational) and the risk-weight schedule. Nothing in either Bank of
    Ghana register prescribes a capital methodology for savings and loans
    companies, finance houses or the Microfinance Bank licence that replaced them
    on 27 January 2026 (``docs/bog_parameter_sources.md`` §2.4 and §7, which
    record the searches). The question is genuinely OPEN, not merely unanswered
    here — so the platform declares nothing, and refuses to seal a ratio whose
    scope is its own placeholder.

    Before this gate the official path checked only ``credit_in_scope``, which
    the code default satisfies by construction, so an SDI with no
    ``sdi_rwa_composition`` row minted a sealed, filable CAR against a code
    default and recorded no ``composition_source`` on the run.

    Raises :class:`SdiCapitalPolicyUnresolved` (409 / ``POLICY_UNRESOLVED``).
    """
    if scope.filable:
        return
    if scope.source != COMPOSITION_CONTROL_PLANE:
        raise _policy_unresolved(
            bank,
            as_of,
            "car_pct",
            [COMPOSITION_PARAM],
            "Which risk classes this institution's capital adequacy ratio must charge "
            "for has not been determined for it. The platform is applying its "
            "documented default — credit risk only — which is a placeholder, not a "
            "supervisory decision: no published instrument states whether a "
            "specialised deposit-taking institution's risk-weighted assets carry a "
            "market-risk or an operational-risk charge, and none is assumed. An "
            "official capital run is filing evidence, so it is refused rather than "
            "sealed against an undetermined scope. Approve the capital-adequacy scope "
            f"({COMPOSITION_PARAM}) in the regulatory-parameter control plane. The "
            "live capital view remains available and states its own scope.",
        )
    if scope.confirmation_status != "confirmed":
        raise _policy_unresolved(
            bank,
            as_of,
            "car_pct",
            [COMPOSITION_PARAM],
            "The approved capital-adequacy scope for this institution is still marked "
            "pending confirmation against a published regulatory instrument, so what "
            "the ratio charges for is not yet a filing conclusion. Confirm "
            f"{COMPOSITION_PARAM} in the regulatory-parameter control plane before "
            "minting an official run. The live capital view remains available.",
        )
    raise _policy_unresolved(
        bank,
        as_of,
        "car_pct",
        [COMPOSITION_PARAM, *scope.pending_parameters],
        "The capital-adequacy scope brings a risk class into charge on a percentage "
        "that is still pending confirmation ("
        + ", ".join(scope.pending_parameters)
        + "). A provisional charge may inform a management view; it may not be sealed "
        "into an official run. Confirm the parameter in the regulatory-parameter "
        "control plane.",
    )


def assert_bucket_map_filable(bank: Bank, as_of: date, bucket_map_source: str) -> None:
    """The taxonomy half of the same claim, wired for the same reason.

    ``bucket_map_source='code_default'`` has always been documented as marking
    the CAR provisional and blocking filing, and its only consumer was the same
    advisory read model as the composition's. The code default is not merely
    unapproved, it is UNSOURCED: the schedule it resembles is Form BSD 5A's
    proposal worksheet, a superseded bank return
    (``docs/bog_parameter_sources.md`` §2.4 — "a trap rather than support").
    Sealing credit risk-weighted assets on it is the D-19 defect one column over.
    """
    if bucket_map_source == BUCKET_MAP_CONTROL_PLANE:
        return
    raise _policy_unresolved(
        bank,
        as_of,
        "total_rwa_ghs",
        [BUCKET_MAP_PARAM],
        "The mapping from product type to risk-weight band is the platform's "
        "documented default, not a mapping approved for this institution, and no "
        "Bank of Ghana schedule of simplified risk-weight bands for specialised "
        "deposit-taking institutions has been published. Credit risk-weighted assets "
        "would rest on it, so an official run is refused rather than sealed. Approve "
        f"{BUCKET_MAP_PARAM} in the regulatory-parameter control plane. The live "
        "capital view remains available and reports the mapping as unconfirmed.",
    )


def assert_official_rwa_scope_governed(db: Session, bank: Bank, as_of: date) -> None:
    """The D-19 gate, on the official capital MINT. A no-op for a bank.

    Banks run the Capital Requirements Directive regime, whose RWA scope the
    directive itself prescribes (¶73(a): credit + market + operational); there is
    nothing here for the control plane to determine and the Basel path is
    untouched. ``institution_class ∈ {bank, sdi}`` and ``capital_regime ∈
    {crd, s29}`` are distinct legal regimes and share no formula.
    """
    if institution_types.institution_class(db, bank) != "sdi":
        return
    assert_scope_filable(bank, as_of, resolve_rwa_scope(db, bank, as_of))
    _, bucket_map_source = resolve_bucket_map(db, bank, as_of)
    assert_bucket_map_filable(bank, as_of, bucket_map_source)


def _out_of_scope_note(db: Session, bank: Bank, risk_class: str, source: str) -> str:
    """Why an omitted risk class contributes nothing — stated, never implied."""
    regulator = regulator_name(db, bank)
    if source == COMPOSITION_CONTROL_PLANE:
        return (
            f"Not charged. The approved capital-adequacy scope for this institution "
            f"does not include {risk_class} risk."
        )
    return (
        f"Not charged, and no charge is assumed. {regulator} has published no "
        f"{risk_class}-risk capital requirement for this class of institution, so "
        "the platform applies none rather than borrowing a bank's. Bringing it "
        "into the ratio is a control-plane decision, not a code change."
    )


@dataclass(frozen=True)
class _Exposures:
    by_bucket: dict[str, Decimal]
    unconverted_count: int
    unconverted_currencies: tuple[str, ...]


def _exposure_by_bucket(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    bucket_map: Mapping[str, str],
) -> _Exposures:
    """Reporting-currency exposure of the current-generation asset positions at
    ``as_of``, grouped by risk-weight bucket.

    Amount rule (identical to ``regulatory_liquidity._currency_ladders``): the
    ingested ``attributes.balance_ghs`` conversion when present, else the native
    ``balance`` ONLY when the position is already in the institution's reporting
    currency. A foreign-currency position with no ingested conversion is
    EXCLUDED and counted — never taken at face value, never converted at an
    invented rate.
    """
    rows = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
        )
    ).all()
    base = base_currency(bank)
    totals: dict[str, Decimal] = {}
    unconverted = 0
    currencies: set[str] = set()
    for snapshot, position in rows:
        bucket = bucket_map.get(position.position_type)
        if bucket is None:
            continue  # not an on-balance-sheet asset — never risk-weighted
        attrs = snapshot.attributes or {}
        raw = attrs.get("balance_ghs")
        if raw not in (None, ""):
            amount = _dec(raw)
        elif (position.currency or "").strip().upper() == base:
            amount = _dec(snapshot.balance)
        else:
            unconverted += 1
            currencies.add((position.currency or "").strip().upper())
            continue
        totals[bucket] = totals.get(bucket, _ZERO) + amount
    return _Exposures(
        by_bucket=totals,
        unconverted_count=unconverted,
        unconverted_currencies=tuple(sorted(currencies)),
    )


def _measurement_unresolved(
    bank: Bank, as_of: date, risk_class: str, measurement: str, *param_codes: str
) -> SdiCapitalPolicyUnresolved:
    """A declared class the module cannot measure refuses — it never contributes 0."""
    return _policy_unresolved(
        bank,
        as_of,
        "total_rwa_ghs",
        list(param_codes),
        f"The capital-adequacy scope declares {risk_class} risk in scope measured as "
        f"{measurement!r}, which this platform does not implement. Supported "
        "measurements are " + ", ".join(SUPPORTED_MEASUREMENTS) + ". A declared "
        "component that cannot be measured is not a zero component — the whole "
        "ratio would be overstated. Correct the composition in the "
        "regulatory-parameter control plane.",
    )


def _risk_class_contributions(
    db: Session, bank: Bank, *, scope: SdiRwaScope, credit_rwa: Decimal
) -> tuple[list[RwaRiskClass], Decimal]:
    """Every known risk class with what it contributed to RWA, and the total.

    Reads the ALREADY-RESOLVED :class:`SdiRwaScope` — every measurement was
    validated and every percentage resolved when the scope was built, so this
    function only reports. Out-of-scope classes are returned as rows contributing
    zero WITH the reason, because the defect this closes is an omission nothing on
    the output surface disclosed.
    """
    ordered = list(KNOWN_RISK_CLASSES) + [
        name for name in sorted(scope.composition) if name not in KNOWN_RISK_CLASSES
    ]
    rows: list[RwaRiskClass] = []
    total = _ZERO
    for risk_class in ordered:
        measurement = scope.composition.get(risk_class)
        if measurement is None:
            rows.append(
                RwaRiskClass(
                    risk_class=risk_class,
                    in_scope=False,
                    measurement=None,
                    rwa_ghs=_ZERO,
                    note=_out_of_scope_note(db, bank, risk_class, scope.source),
                )
            )
            continue
        if measurement == MEASURE_BUCKET_WEIGHTED_EXPOSURE:
            total += credit_rwa
            rows.append(
                RwaRiskClass(
                    risk_class=risk_class,
                    in_scope=True,
                    measurement=measurement,
                    rwa_ghs=credit_rwa,
                    note=(
                        "On-balance-sheet asset exposure, risk-weighted by the "
                        "simplified bucket table."
                    ),
                )
            )
            continue
        charge = scope.pct_of_credit_rwa[risk_class]
        amount = credit_rwa * charge / _HUNDRED
        total += amount
        rows.append(
            RwaRiskClass(
                risk_class=risk_class,
                in_scope=True,
                measurement=measurement,
                rwa_ghs=amount,
                note=(
                    f"Charged at {charge}% of credit risk-weighted assets, as "
                    "configured for this institution."
                ),
            )
        )
    return rows, total


def compute_sdi_capital_summary(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> SdiCapitalSummary:
    """The s.29 capital-adequacy summary: NOF ÷ RWA vs the s.29 floor.

    RWA is the sum over the DECLARED risk-class composition
    (:func:`resolve_rwa_scope` — the same object the official filing run consumes,
    so the two paths cannot charge for different things). Classes outside it
    contribute nothing and
    are still reported, with the reason, on ``risk_classes`` — the ratio states its
    own scope instead of leaving the reader to infer it from the bands.

    Raises :class:`SdiCapitalPolicyUnresolved` when the floor, any applicable
    bucket weight, or the charge for a declared risk class cannot be established
    from the control plane.
    """
    car_min_param = rp.resolve(db, bank, "car_min", as_of=as_of)
    car_min = car_min_param.normalized_value
    if car_min is None:
        raise _policy_unresolved(
            bank,
            as_of,
            "car_min_pct",
            ["car_min"],
            "The Act 930 s.29 minimum capital adequacy ratio resolved with no value, so "
            "there is no floor to measure this institution against. A missing floor is "
            "not a zero floor — every ratio would pass. Set the car_min value in the "
            "regulatory-parameter control plane.",
        )

    nof = _net_own_funds(db, ctx, bank, as_of)
    bucket_map, bucket_map_source = resolve_bucket_map(db, bank, as_of)
    scope = resolve_rwa_scope(db, bank, as_of)

    bands: list[RiskWeightBand] = []
    pending: list[str] = list(scope.pending_parameters)
    exposures = _Exposures(by_bucket={}, unconverted_count=0, unconverted_currencies=())
    credit_rwa = _ZERO
    # --- credit: the only class measured from the book itself ---------------
    if scope.credit_in_scope:
        exposures = _exposure_by_bucket(db, ctx, bank, as_of, bucket_map)
        unresolved: list[str] = []
        for bucket in sorted(exposures.by_bucket):
            code = f"risk_weight_{bucket}"
            exposure = exposures.by_bucket[bucket]
            resolved = rp.try_resolve(db, bank, code, as_of=as_of)
            weight = resolved.normalized_value if resolved is not None else None
            if resolved is None or weight is None:
                # No weight established. A 100% assumption is a regulatory number
                # invented in code (forensic audit 2026-08-21 section 10), so the
                # ratio refuses — unless the band is empty, in which case no weight
                # could change risk-weighted assets and there is nothing to refuse.
                if exposure != _ZERO:
                    unresolved.append(code)
                continue
            if resolved.is_pending:
                pending.append(code)
            rwa = exposure * weight / _HUNDRED
            credit_rwa += rwa
            bands.append(
                RiskWeightBand(bucket, weight, exposure, rwa, resolved.confirmation_status)
            )

        if unresolved:
            raise _policy_unresolved(
                bank,
                as_of,
                "total_rwa_ghs",
                sorted(set(unresolved)),
                "Risk-weighted assets cannot be established: no risk weight is configured "
                "for " + ", ".join(sorted(set(unresolved))) + ". The exposure in those "
                "buckets is real, so leaving them out would understate risk-weighted assets "
                "and overstate capital adequacy. Configure the weights in the "
                "regulatory-parameter control plane.",
            )

    # --- every other declared class, plus the disclosure of what is left out -
    risk_classes, total_rwa = _risk_class_contributions(
        db, bank, scope=scope, credit_rwa=credit_rwa
    )

    computable = nof != _ZERO and total_rwa > _ZERO
    car = (nof / total_rwa * _HUNDRED) if total_rwa > _ZERO else None
    if car is None:
        status_code = "na"
    elif car >= car_min:
        status_code = "green"
    else:
        status_code = "red"

    return SdiCapitalSummary(
        as_of=as_of,
        net_own_funds_ghs=nof,
        total_rwa_ghs=total_rwa,
        car_pct=(car.quantize(Decimal("0.01")) if car is not None else None),
        car_min_pct=car_min,
        status=status_code,
        car_min_confirmation=car_min_param.confirmation_status,
        computable=computable,
        bands=bands,
        pending_parameters=sorted(set(pending)),
        bucket_map_source=bucket_map_source,
        unconverted_position_count=exposures.unconverted_count,
        unconverted_currencies=exposures.unconverted_currencies,
        composition_source=scope.source,
        risk_classes=risk_classes,
    )
