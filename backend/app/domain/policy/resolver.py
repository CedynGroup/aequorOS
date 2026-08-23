"""The Policy Resolver (shared primitive P2) — one chain, no scattered conditionals.

Every governed regulatory number in the platform is selected by the SAME chain::

    Jurisdiction -> Regulator -> Institution Type -> Regime -> Return Family
                 -> Parameter Set -> Effective Date

Before this module that chain existed as conditionals sprinkled through the
engines: a ``institution_class == "sdi"`` branch here, a ``(bank.jurisdiction_code
or "GH")`` there, a ``car_min`` clamp in one loader and a raw read of the same code
in another. Two call sites resolving "the CAR floor" could — and did — disagree.

This module is the single expression of the chain. It is **pure**: no SQLAlchemy,
no FastAPI, no ``app.services`` import, no I/O (CLAUDE.md: ``app/domain/*`` is pure
and must stay that way). The database adapter lives in
``app/services/regulatory_parameters.py``, which fetches candidate rows and hands
them here; the institution/jurisdiction adapters live in
``app/services/institution_types.py`` and ``app/services/jurisdictions.py``.

What the chain guarantees
-------------------------
1. **No silent default.** A link that cannot be established is
   :class:`PolicyUnresolvedError` (carrying WS-A's ``POLICY_UNRESOLVED``
   outcome), never a substituted value. A regulatory number is never invented.
2. **Effective dating.** Selection is always ``effective_from <= as_of`` and
   ``effective_to`` null-or-after — the one date rule, matching
   ``app.services.params.get_active_params`` exactly.
3. **Versioned + attributable.** The winning row carries its own identity,
   generation date, maker-checker status and legal citation; the resolution
   carries the layer it came from. :meth:`PolicyResolution.provenance` is the
   audit record an examiner reads.
4. **Tighten-only overrides.** A tenant board register may make a governed
   requirement STRICTER, never weaker — enforced for every code in
   :data:`PARAMETER_DIRECTION` by :func:`clamp_overrides`, not per-code at
   individual call sites.

Precedence (most specific wins)::

    tenant board override (clamped, never weaker)
      > institution_type row (licence-specific)
        > institution_class row (bank | sdi)
          > POLICY_UNRESOLVED

Provenance integration point (WS-A)
-----------------------------------
``PolicyResolution.provenance()`` returns a stable, JSON-ready dict. When WS-A's
typed provenance struct lands, construct it from exactly these keys and keep the
wire names — downstream surfaces and digests read them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.domain.authority.outcomes import (
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    outcome,
)

__all__ = [
    "PARAMETER_DIRECTION",
    "ClampRecord",
    "Direction",
    "ParameterCandidate",
    "PolicyLayer",
    "PolicyResolution",
    "PolicyScope",
    "PolicyUnresolvedError",
    "ClampReport",
    "clamp_overrides",
    "direction_for",
    "governed_codes",
    "is_active_on",
    "policy_unresolved",
    "resolution_order",
    "select_active",
    "tighten",
]


# --------------------------------------------------------------------------
# 1. The chain key
# --------------------------------------------------------------------------


class PolicyLayer(StrEnum):
    """Which link of the chain supplied the value that was used."""

    #: Per-tenant board register (``ParamCapitalThreshold`` / ``ParamLiquidityThreshold`` …).
    TENANT_OVERRIDE = "tenant_override"
    #: Licence-specific global default (``scope_type='institution_type'``).
    INSTITUTION_TYPE = "institution_type"
    #: Coarse regime default (``scope_type='institution_class'``: bank | sdi).
    INSTITUTION_CLASS = "institution_class"


#: The scope axes a global parameter row may key on, most specific first. This
#: ordering IS the precedence — do not reorder without changing the docstring
#: contract above, every engine depends on licence beating class.
SCOPE_PRECEDENCE: tuple[str, ...] = (
    PolicyLayer.INSTITUTION_TYPE.value,
    PolicyLayer.INSTITUTION_CLASS.value,
)


@dataclass(frozen=True, slots=True)
class PolicyScope:
    """The fully-resolved chain key for one institution at one effective date.

    Built by ``app.services.regulatory_parameters.policy_scope`` from the bank's
    jurisdiction registry row and institution-type registry row. Every field is
    REQUIRED and none carries a default: a scope with a missing link must not be
    constructible, because that is precisely how ``jurisdiction_code="GH"``
    defaults made a Nigerian bank report in cedis.
    """

    #: Jurisdiction registry code, e.g. ``GH`` — link 1.
    jurisdiction_code: str
    #: Reporting currency for the jurisdiction/bank, e.g. ``GHS``.
    currency: str
    #: Short regulator form for display/provenance, e.g. ``BoG`` — link 2.
    regulator_short: str
    #: Full central-bank name, e.g. ``Bank of Ghana``.
    regulator_name: str
    #: Licence class code, e.g. ``universal_bank`` / ``savings_and_loans`` — link 3.
    institution_type: str
    #: Coarse regime axis: ``bank`` | ``sdi`` — link 4.
    institution_class: str
    #: Capital regime: ``crd`` (Basel) | ``s29`` (Act 930) — link 4.
    capital_regime: str
    #: BoG return family filed: ``bsd`` | ``sdi`` — link 5.
    return_family: str
    #: Whether the LMTD Table-1 ratios BIND rather than merely monitor.
    liquidity_binding: bool
    #: The effective date the parameter set is resolved as of — link 7.
    as_of: date

    def __post_init__(self) -> None:
        for field_name in (
            "jurisdiction_code",
            "currency",
            "institution_type",
            "institution_class",
            "capital_regime",
            "return_family",
        ):
            if not str(getattr(self, field_name) or "").strip():
                msg = (
                    f"PolicyScope.{field_name} is required and has no default: a policy "
                    "chain with a missing link must fail, never resolve to a plausible "
                    "regime."
                )
                raise ValueError(msg)

    def key(self) -> tuple[str, str, str, str, str]:
        """Stable identity of the chain (excluding the effective date)."""
        return (
            self.jurisdiction_code,
            self.regulator_short,
            self.institution_type,
            self.institution_class,
            self.return_family,
        )

    def describe(self) -> str:
        """One-line human form for an error message or a log line."""
        return (
            f"{self.jurisdiction_code}/{self.regulator_short} "
            f"{self.institution_type} ({self.institution_class}/{self.capital_regime}, "
            f"return_family={self.return_family}) as of {self.as_of.isoformat()}"
        )


def resolution_order(scope: PolicyScope) -> tuple[tuple[str, str], ...]:
    """The ``(scope_type, scope_key)`` pairs to try, most specific first.

    THE chain, expressed once. A DB adapter iterates this instead of writing its
    own "try the licence row, then the class row" logic — which is how the two
    layers came to be searched in different orders in different modules.
    """
    return (
        (PolicyLayer.INSTITUTION_TYPE.value, scope.institution_type),
        (PolicyLayer.INSTITUTION_CLASS.value, scope.institution_class),
    )


# --------------------------------------------------------------------------
# 2. Candidate rows and effective dating
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParameterCandidate:
    """One effective-dated governed parameter row, storage-agnostic.

    Mirrors ``app.models.regulatory_parameter.RegulatoryParameter`` field-for-field
    but with no ORM dependency, so the selection rules are testable without a
    database and reusable if the control plane is ever fed from elsewhere.
    """

    param_code: str
    scope_type: str
    scope_key: str
    jurisdiction_code: str
    unit: str
    source_citation: str
    confirmation_status: str
    effective_from: date
    parameter_id: str
    value: Decimal | None = None
    value_json: Mapping[str, Any] | None = None
    effective_to: date | None = None
    status: str = "approved"

    @property
    def is_pending(self) -> bool:
        """A documented default standing in for an unconfirmed regulatory number."""
        return self.confirmation_status == "pending"


def is_active_on(candidate: ParameterCandidate, as_of: date) -> bool:
    """The single active-window rule: ``effective_from <= as_of < effective_to``.

    Identical to ``app.services.params.get_active_params`` — one date rule in the
    codebase. ``effective_to`` null means open-ended.
    """
    if candidate.effective_from > as_of:
        return False
    return candidate.effective_to is None or candidate.effective_to > as_of


def select_active(
    candidates: Iterable[ParameterCandidate],
    scope: PolicyScope,
    param_code: str,
) -> ParameterCandidate | None:
    """Pick the winning row for ``param_code`` under ``scope``, or ``None``.

    Applies, in order: jurisdiction match, ``approved`` status (drafts never
    resolve — four-eyes is a precondition of a value being used, not a display
    flag), the active window on ``scope.as_of``, then scope precedence
    (institution_type beats institution_class), then the newest generation, then
    a stable ``parameter_id`` tie-break so resolution is deterministic and an
    ``input_hash`` built over it is reproducible.
    """
    eligible = [
        candidate
        for candidate in candidates
        if candidate.param_code == param_code
        and candidate.jurisdiction_code == scope.jurisdiction_code
        and candidate.status == "approved"
        and is_active_on(candidate, scope.as_of)
    ]
    if not eligible:
        return None
    order = {scope_type: index for index, (scope_type, _) in enumerate(resolution_order(scope))}
    wanted = dict(resolution_order(scope))
    matching = [
        candidate
        for candidate in eligible
        if wanted.get(candidate.scope_type) == candidate.scope_key
    ]
    if not matching:
        return None
    matching.sort(
        key=lambda candidate: (
            order[candidate.scope_type],
            -candidate.effective_from.toordinal(),
            candidate.parameter_id,
        )
    )
    return matching[0]


# --------------------------------------------------------------------------
# 3. The resolution result + provenance
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyResolution:
    """A governed value plus the whole chain that produced it."""

    param_code: str
    scope: PolicyScope
    layer: PolicyLayer
    value: Decimal | None
    value_json: Mapping[str, Any] | None
    unit: str
    source_citation: str
    confirmation_status: str
    scope_type: str
    scope_key: str
    effective_from: date
    parameter_id: str
    #: Set when a tenant board override was clamped by the governed value.
    clamped_from: Decimal | None = None

    @property
    def is_pending(self) -> bool:
        return self.confirmation_status == "pending"

    @property
    def was_clamped(self) -> bool:
        return self.clamped_from is not None

    @property
    def decimal(self) -> Decimal:
        if self.value is None:
            raise PolicyUnresolvedError(
                policy_unresolved(
                    self.param_code,
                    self.scope,
                    reason=f"Parameter {self.param_code!r} resolved with no scalar value.",
                )
            )
        return self.value

    @property
    def normalized_value(self) -> Decimal | None:
        """The scalar with a ``Numeric(18,6)`` round-trip's trailing zeros stripped
        (``Decimal("80.000000") -> Decimal("80")``) so a control-plane value is
        byte-identical to the in-code constant it replaced when it lands in
        generated return content or a content digest."""
        if self.value is None:
            return None
        v = self.value
        return v.quantize(Decimal(1)) if v == v.to_integral_value() else v.normalize()

    def provenance(self) -> dict[str, Any]:
        """The audit record: which parameter, which version, what source, confirmed?

        **WS-A integration point.** These keys are the wire contract — when the
        typed provenance struct lands, build it from exactly these names.
        """
        return {
            "param_code": self.param_code,
            "value": None if self.value is None else str(self.normalized_value),
            "unit": self.unit,
            "layer": self.layer.value,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "jurisdiction_code": self.scope.jurisdiction_code,
            "regulator": self.scope.regulator_short,
            "institution_type": self.scope.institution_type,
            "institution_class": self.scope.institution_class,
            "capital_regime": self.scope.capital_regime,
            "return_family": self.scope.return_family,
            "effective_from": self.effective_from.isoformat(),
            "as_of": self.scope.as_of.isoformat(),
            "source_citation": self.source_citation,
            "confirmation_status": self.confirmation_status,
            "parameter_id": self.parameter_id,
            "clamped": self.was_clamped,
            "clamped_from": None if self.clamped_from is None else str(self.clamped_from),
        }


def from_candidate(
    candidate: ParameterCandidate,
    scope: PolicyScope,
    *,
    clamped_from: Decimal | None = None,
    layer: PolicyLayer | None = None,
) -> PolicyResolution:
    """Lift a winning candidate row into a :class:`PolicyResolution`."""
    return PolicyResolution(
        param_code=candidate.param_code,
        scope=scope,
        layer=layer or PolicyLayer(candidate.scope_type),
        value=candidate.value,
        value_json=candidate.value_json,
        unit=candidate.unit,
        source_citation=candidate.source_citation,
        confirmation_status=candidate.confirmation_status,
        scope_type=candidate.scope_type,
        scope_key=candidate.scope_key,
        effective_from=candidate.effective_from,
        parameter_id=candidate.parameter_id,
        clamped_from=clamped_from,
    )


# --------------------------------------------------------------------------
# 4. Fail-closed vocabulary
# --------------------------------------------------------------------------


def policy_unresolved(
    param_code: str,
    scope: PolicyScope | None = None,
    *,
    reason: str | None = None,
    items: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
) -> OutcomeDetail:
    """A ``POLICY_UNRESOLVED`` outcome for a link of the chain that did not resolve.

    Uses WS-A's authority vocabulary (``app/domain/authority/outcomes.py``) so a
    policy failure is the same shape as every other fail-closed state and blocks
    filing by the same rule.
    """
    where = f" for {scope.describe()}" if scope is not None else ""
    detail_context: dict[str, Any] = dict(context or {})
    if scope is not None:
        detail_context.setdefault("jurisdiction_code", scope.jurisdiction_code)
        detail_context.setdefault("institution_type", scope.institution_type)
        detail_context.setdefault("institution_class", scope.institution_class)
        detail_context.setdefault("as_of", scope.as_of.isoformat())
    return outcome(
        OutcomeState.POLICY_UNRESOLVED,
        metric_id=param_code,
        reason=reason
        or (
            f"No approved, effective regulatory parameter {param_code!r}{where}. "
            "It must be configured in the regulatory-parameter control plane — a "
            "regulatory number is never substituted."
        ),
        items=tuple(items) or (f"param:{param_code}",),
        context=detail_context,
    )


class PolicyUnresolvedError(NotComputable):
    """Raised when the chain cannot establish a governed value.

    Subclasses WS-A's :class:`~app.domain.authority.outcomes.NotComputable`, so a
    service boundary that already handles fail-closed outcomes handles this too
    and can persist the reason against the run rather than logging a string.
    """


# --------------------------------------------------------------------------
# 5. Tighten-only enforcement (generalised)
# --------------------------------------------------------------------------


class Direction(StrEnum):
    """The conservative direction of a governed value."""

    #: A minimum. A tenant override must be at least as high (effective = max).
    FLOOR = "floor"
    #: A maximum/limit. A tenant override must be at most as high (effective = min).
    CEILING = "ceiling"


#: Every governed code and the direction a tenant board override may move it.
#:
#: A code listed here is clamped **everywhere** the generalised enforcement runs
#: (``clamp_overrides``), not only where an individual call site remembered to
#: clamp it. Before 2026-08-21 only ``car_min`` and the eight LMTD floors were
#: actually enforced, in two hand-written call sites, while the other sixteen
#: codes were declared and silently unenforced — and ``regulatory_forecasting``
#: read ``car_min`` raw while ``regulatory_capital`` clamped the same code.
#:
#: A code ABSENT from this map carries no tightening constraint (e.g. an internal
#: early-warning trigger a board sets wherever it wants). Absence must be a
#: decision, not an oversight.
PARAMETER_DIRECTION: dict[str, str] = {
    # capital + leverage minima
    "car_min": Direction.FLOOR.value,
    "cet1_min": Direction.FLOOR.value,
    "tier1_min": Direction.FLOOR.value,
    "leverage_min": Direction.FLOOR.value,
    # liquidity minima
    "lcr_min": Direction.FLOOR.value,
    "nsfr_min": Direction.FLOOR.value,
    "primary_liquidity_reserve_pct": Direction.FLOOR.value,
    "secondary_liquidity_reserve_pct": Direction.FLOOR.value,
    "statutory_reserve_fund_pct": Direction.FLOOR.value,
    # exposure limits (a board may only make them stricter, i.e. lower)
    "single_obligor_limit_pct": Direction.CEILING.value,
    "large_exposure_limit_pct": Direction.CEILING.value,
    "related_party_limit_pct": Direction.CEILING.value,
    # provisioning minima (a board may only over-provide)
    "prov_standard": Direction.FLOOR.value,
    "prov_olem": Direction.FLOOR.value,
    "prov_substandard": Direction.FLOOR.value,
    "prov_doubtful": Direction.FLOOR.value,
    "prov_loss": Direction.FLOOR.value,
    # LMTD prudential-ratio floors
    "narrow_to_volatile": Direction.FLOOR.value,
    "broad_to_volatile": Direction.FLOOR.value,
    "narrow_to_short_term": Direction.FLOOR.value,
    "broad_to_short_term": Direction.FLOOR.value,
    "narrow_to_total_assets": Direction.FLOOR.value,
    "broad_to_total_assets": Direction.FLOOR.value,
    "narrow_to_total_deposits": Direction.FLOOR.value,
    "broad_to_total_deposits": Direction.FLOOR.value,
    # Data-integrity controls. Not a published regulatory number, but governed
    # all the same: ``balance_identity_tolerance_pct`` is the width of the
    # |assets - (liabilities + equity)| gap a book may carry and still produce a
    # FILED figure (app/services/reconciliation.py). A larger tolerance admits a
    # more broken book, so tightening means SMALLER — ceiling. It was seeded and
    # documented as tighten-only from the start but omitted from this map until
    # 2026-08-22 (audit NEW-39), which left the one control between a broken book
    # and a filed return as the only governed code a board could WIDEN.
    "balance_identity_tolerance_pct": Direction.CEILING.value,
}


def governed_codes() -> frozenset[str]:
    """Every parameter code under tighten-only enforcement."""
    return frozenset(PARAMETER_DIRECTION)


def direction_for(param_code: str) -> Direction | None:
    """The declared direction of ``param_code``, or ``None`` if unconstrained."""
    raw = PARAMETER_DIRECTION.get(param_code)
    return Direction(raw) if raw is not None else None


def tighten(param_code: str, tenant_value: Decimal, control_value: Decimal) -> Decimal:
    """The more conservative of a tenant board override and the governed value.

    A ``floor`` code returns the higher value, a ``ceiling`` code the lower; an
    undeclared code returns the tenant value unchanged. Pure and total — the
    building block :func:`clamp_overrides` applies across a whole register.
    """
    direction = direction_for(param_code)
    if direction is Direction.FLOOR:
        return max(tenant_value, control_value)
    if direction is Direction.CEILING:
        return min(tenant_value, control_value)
    return tenant_value


@dataclass(frozen=True, slots=True)
class ClampRecord:
    """Evidence that one tenant override was weaker than the governed value.

    Recorded, not silently applied: a board register that tried to weaken a
    regulatory floor is a governance event an operator must be able to see.
    """

    param_code: str
    direction: Direction
    tenant_value: Decimal
    control_value: Decimal
    effective_value: Decimal
    source_citation: str = ""
    confirmation_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "param_code": self.param_code,
            "direction": self.direction.value,
            "tenant_value": str(self.tenant_value),
            "control_value": str(self.control_value),
            "effective_value": str(self.effective_value),
            "source_citation": self.source_citation,
            "confirmation_status": self.confirmation_status,
        }


@dataclass(frozen=True, slots=True)
class ClampReport:
    """The result of clamping a whole tenant register in one pass."""

    values: dict[str, Decimal]
    clamped: tuple[ClampRecord, ...]

    @property
    def any_clamped(self) -> bool:
        return bool(self.clamped)

    def codes_clamped(self) -> tuple[str, ...]:
        return tuple(record.param_code for record in self.clamped)


def clamp_overrides(
    tenant_values: Mapping[str, Decimal],
    control_values: Mapping[str, Decimal | None],
) -> ClampReport:
    """Apply tighten-only enforcement across an ENTIRE tenant register at once.

    This is the generalisation the audit asked for (QA 2026-08-20 P1-5): instead
    of one hand-written clamp per code per call site — which covered 9 of the 25
    governed codes and disagreed between two modules on the same code — a loader
    hands its whole threshold dict here and every governed code with a
    control-plane counterpart is clamped in one place.

    ``control_values`` may omit a code or map it to ``None``; that code passes
    through unchanged (no governed value is seeded for it, so there is nothing to
    tighten against — and a value is never invented to create one).
    """
    effective: dict[str, Decimal] = dict(tenant_values)
    records: list[ClampRecord] = []
    for param_code, tenant_value in tenant_values.items():
        direction = direction_for(param_code)
        if direction is None:
            continue
        control_value = control_values.get(param_code)
        if control_value is None:
            continue
        tightened = tighten(param_code, tenant_value, control_value)
        effective[param_code] = tightened
        if tightened != tenant_value:
            records.append(
                ClampRecord(
                    param_code=param_code,
                    direction=direction,
                    tenant_value=tenant_value,
                    control_value=control_value,
                    effective_value=tightened,
                )
            )
    return ClampReport(values=effective, clamped=tuple(records))
