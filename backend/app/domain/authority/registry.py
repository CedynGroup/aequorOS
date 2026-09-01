"""Metric Authority Registry (primitive P1).

**What this is.** A machine-readable declaration of *which engine is allowed to
produce each material financial figure*. For every metric, exactly one engine is
the authority under one (regime x methodology). Anything else that computes the
same concept is either a declared alternate methodology with documented
divergence, or it is forbidden.

**Why it exists.** The 2026-08-21 forensic audits found four executable
calculation planes and no way for the code to state which one owns a number.
The architecture audit's own words: *"the architecture has no universal 'metric
authority registry' to make this explicit or fail when an engine/template
mapping diverges."* This module is that registry.

**What this is NOT.** It changes no formula. It is descriptive: it records what
the repository already does and makes the duplicates visible and testable.
WS-B / WS-C own the engine changes.

Design rules
------------

1. **One authority per (metric_id x regime x methodology_id).** Registering a
   second entry for the same triple raises :class:`DuplicateAuthorityError`.
   ``tests/domain/authority/test_registry.py`` proves it fails loudly.
2. **A genuine alternate methodology is a first-class entry, never a silent
   duplicate.** It carries its own ``methodology_id``, its own authority
   citation, and a mandatory :class:`MethodologyDivergence` saying *why* it
   differs, in which direction, and whether the divergence is
   ``accepted_by_authority`` or an ``unresolved_audit_finding``.
3. **Never invent a regulatory rule.** Where the repository does not establish a
   legal basis, the entry carries
   :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED` rather than a guess.
4. **Engines are import-checkable.** ``calculation_engine`` is a
   ``module.path:callable`` string; the test suite imports every one of them.

Repo reality that contradicts the audit's shorthand — read this
---------------------------------------------------------------

* The audit says "BSD3 LCR". There is no LCR on the official ``BSD3A``/``BSD3B``
  forms — those are the **Large Exposures** returns. Migration ``202608150013``
  recoded the legacy liquidity/capital reconstructions to ``LCR-NSFR`` and
  ``CAR-RWA``; only their ``template_id`` strings still read
  ``bog-bsd3-liquidity-v1`` / ``bog-bsd2-capital-v1``. Wherever the audit says
  "BSD3 LCR", this registry says ``LCR-NSFR``.
* The audit's "Tier 2 / CET1 / Tier 1 **amounts**" are NOT keys in
  ``RegulatoryRun.metrics``. They persist as ``RegulatoryLineItem`` rows
  (sections ``capital_component`` / ``ratio``). Their entries here say so.

The divergences you must not "fix" by asserting equality
--------------------------------------------------------

``lcr_pct`` and ``car_pct`` each exist more than once, deliberately:

* ``lcr_pct`` / ``basel_bog_liquidity_run`` — the LCR-NSFR return's LCR. It DOES
  cap inflows: ``lcr_inflow_cap_pct`` is a required threshold
  (``regulatory_liquidity._REQUIRED_THRESHOLDS``) applied unconditionally at
  ``app/domain/liquidity/engine.py:264``, and the preview row is labelled
  "After Cap".
* ``lcr_pct`` / ``lmtd_table11_capped`` — the LMT Table 11 by-currency LCR.
  It caps too. The two divergences are (a) the cap SOURCE — a governed,
  effective-dated ``lcr_inflow_cap_pct`` parameter versus the hard-coded
  ``le_generation._LCR_INFLOW_CAP = Decimal("0.75")`` — and (b) the cap
  GRANULARITY — one aggregate cap across the whole book versus a separate cap
  per currency column. **Neither methodology is "the uncapped one".** Saying so
  invites an engineer to add a cap that is already there, or to remove one
  believing it was never intended.
* ``car_pct`` / ``crd_basel_capital_run`` — Basel III CAR from the capital
  engine.
* ``car_pct`` / ``bog_bsd5a_form_ratio`` — BoG's own printed BSD5A ratio
  ``E70 = E25/E69``. Different add-on rules by design; the repository already
  pins the inequality (``tests/services/bog_forms/test_bsd5.py``: *"by
  construction, not by accident"*).
* ``car_pct`` / ``act930_s29_nof_rwa`` — the SDI regime's NOF / simplified RWA.

All of the above are ``accepted_by_authority``. Two entries are instead
``unresolved_audit_finding`` and are registered ``ADVISORY_ONLY`` so they can
never be filed while the divergence stands: the forecast Year-0 ``car_pct`` and
``lcr_pct`` path values.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "ACCEPTED_BY_AUTHORITY",
    "CLASS_SPECIFIC_REGIMES",
    "EXTERNAL_REGULATORY_VERIFICATION_REQUIRED",
    "REGISTRY",
    "REQUIRED_AUTHORITY_FIELDS",
    "UNRESOLVED_AUDIT_FINDING",
    "AdvisoryDesignation",
    "AuthorityKey",
    "CodeEvidence",
    "CompletenessFailure",
    "DuplicateAuthorityError",
    "InstitutionClass",
    "MethodologyDivergence",
    "MetricAuthority",
    "MetricAuthorityRegistry",
    "MetricFamily",
    "Regime",
    "UnknownMetricError",
    "all_authorities",
    "authorities_for_metric",
    "check_completeness",
    "get_authority",
    "multi_authority_metrics",
]


#: Sentinel for a field whose regulatory basis is not established anywhere in
#: this repository. An entry carrying it IS registered (so the metric is not
#: invisible) but is explicitly not certified.
EXTERNAL_REGULATORY_VERIFICATION_REQUIRED = "EXTERNAL_REGULATORY_VERIFICATION_REQUIRED"

#: Divergence resolution states.
ACCEPTED_BY_AUTHORITY = "accepted_by_authority"
"""Both methods are correct under their own regulatory authority. Never assert
equality; never "reconcile" one into the other."""

UNRESOLVED_AUDIT_FINDING = "unresolved_audit_finding"
"""The forensic audit flagged this as a defect, not a legitimate alternate.
WS-B / WS-C must resolve it. Until then the alternate is ADVISORY_ONLY."""

_RESOLUTION_STATES = frozenset({ACCEPTED_BY_AUTHORITY, UNRESOLVED_AUDIT_FINDING})

#: Governance fields every entry must answer. An entry may answer with
#: :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED` — that is an honest "no
#: legal basis is established in this repository". It may not answer with a
#: blank, which is indistinguishable from a figure nobody ever governed.
#: ``authority_reference`` is the field the forensic re-audit calls
#: ``source_citation`` (see :attr:`MetricAuthority.source_citation`).
REQUIRED_AUTHORITY_FIELDS: tuple[str, ...] = (
    "metric_id",
    "methodology_id",
    "jurisdiction",
    "regulator",
    "authority_reference",
    "policy_resolver",
    "calculation_engine",
    "calculation_version",
)


class MetricFamily(StrEnum):
    """Coarse grouping used by dashboards, packages and this registry."""

    CAPITAL = "capital"
    LIQUIDITY = "liquidity"
    IRRBB = "irrbb"
    FX = "fx"
    FTP = "ftp"
    FORECAST = "forecast"
    STRESS = "stress"
    CREDIT = "credit"


class InstitutionClass(StrEnum):
    """Tenant class the authority applies to.

    Values match ``institution_types.institution_class`` in the repository
    (``bank`` / ``sdi``). ``ALL`` means class-neutral: one engine serves both.
    """

    BANK = "bank"
    SDI = "sdi"
    ALL = "all"


class Regime(StrEnum):
    """The legal / prudential regime under which the figure is computed.

    ``CRD_BASEL`` and ``ACT930_S29`` mirror
    ``institution_types.capital_regime`` (``crd`` / ``s29``) and are genuinely
    different law — the audit's "regime duplication" finding, which must NOT be
    consolidated. ``LMTD`` is the Liquidity Monitoring Tools Directive, which
    has its own methodology for measures that also appear elsewhere.
    ``IFRS9`` is an accounting standard, shared by both classes.
    ``ADVISORY_INTERNAL`` covers figures with no external regulatory authority
    (board / ALCO / management analytics).
    """

    CRD_BASEL = "crd"
    ACT930_S29 = "s29"
    LMTD = "lmtd"
    IFRS9 = "ifrs9"
    ADVISORY_INTERNAL = "advisory_internal"


class AdvisoryDesignation(StrEnum):
    """Whether the figure may be filed, or is analysis only."""

    FILED = "filed"
    """Authoritative for regulatory submission."""

    SUPERVISORY_MONITORING = "supervisory_monitoring"
    """Reviewed by the supervisor / board but not a filed return line today."""

    ADVISORY_ONLY = "advisory_only"
    """Internal analysis. Must never reach a filing."""


#: Regimes that are the law for exactly ONE class of institution, and the class
#: they bind. A universal bank is capitalised under the BoG Capital Requirements
#: Directive; a specialised deposit-taking institution is capitalised under
#: Act 930 s.29 and the NBFI Business Rules. They are different statutes with
#: different definitions of own funds, so an entry may not straddle them —
#: including via :attr:`InstitutionClass.ALL`, which
#: ``MetricAuthorityRegistry.for_institution_class`` hands to BOTH classes and
#: is therefore how a regime silently inherits an authority it has no claim to.
#: Regimes absent from this mapping (``lmtd``, ``ifrs9``, ``advisory_internal``)
#: are deliberately not constrained here: a directive or accounting standard
#: that binds more than one class is a fact about the source, not a leak.
CLASS_SPECIFIC_REGIMES: Mapping[Regime, InstitutionClass] = {
    Regime.CRD_BASEL: InstitutionClass.BANK,
    Regime.ACT930_S29: InstitutionClass.SDI,
}


@dataclass(frozen=True, slots=True)
class MethodologyDivergence:
    """Why an alternate methodology legitimately produces a different number.

    Mandatory on every entry that is not a metric's primary methodology.
    Without it, an alternate is indistinguishable from an accidental duplicate —
    which is exactly what the audit found.
    """

    versus_methodology_id: str
    authority_reference: str
    """Citation for THIS methodology (e.g. ``LMTD 39-43 (Table 11)``)."""

    reason: str
    """The mechanical difference that produces the divergence."""

    direction: str
    """``lower`` / ``higher`` / ``either`` — sign of this versus its counterpart."""

    reconciliation_rule: str
    """How the two relate. Often: they are NOT reconciled by equality."""

    resolution_status: str = ACCEPTED_BY_AUTHORITY
    equality_assertion_forbidden: bool = True
    """True means: never write a test asserting these two are equal."""

    evidence: tuple[str, ...] = ()
    """Files / tests that already demonstrate the divergence."""

    def __post_init__(self) -> None:
        for name in ("versus_methodology_id", "authority_reference", "reason", "direction"):
            if not getattr(self, name):
                raise ValueError(f"MethodologyDivergence.{name} must be non-empty")
        if self.direction not in {"lower", "higher", "either"}:
            raise ValueError(
                "MethodologyDivergence.direction must be 'lower', 'higher' or 'either'"
            )
        if self.resolution_status not in _RESOLUTION_STATES:
            raise ValueError(
                "MethodologyDivergence.resolution_status must be "
                f"one of {sorted(_RESOLUTION_STATES)}"
            )
        if not self.reconciliation_rule:
            raise ValueError("MethodologyDivergence.reconciliation_rule must be non-empty")

    @property
    def is_accepted(self) -> bool:
        return self.resolution_status == ACCEPTED_BY_AUTHORITY

    def to_dict(self) -> dict[str, object]:
        return {
            "versus_methodology_id": self.versus_methodology_id,
            "authority_reference": self.authority_reference,
            "reason": self.reason,
            "direction": self.direction,
            "reconciliation_rule": self.reconciliation_rule,
            "resolution_status": self.resolution_status,
            "equality_assertion_forbidden": self.equality_assertion_forbidden,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class AuthorityKey:
    """The uniqueness key. Exactly one authority may hold it."""

    metric_id: str
    regime: Regime
    methodology_id: str

    def __str__(self) -> str:
        return f"{self.metric_id}@{self.regime.value}/{self.methodology_id}"


@dataclass(frozen=True, slots=True)
class MetricAuthority:
    """The single declared owner of one metric under one regime + methodology."""

    # -- identity ---------------------------------------------------------
    metric_id: str
    """The metric key verbatim as the repository produces it — a
    ``RegulatoryRun.metrics`` key where a run exists, otherwise the field name
    on the computing service's result object."""

    metric_family: MetricFamily
    institution_class: InstitutionClass
    jurisdiction: str
    """ISO-3166 alpha-2, or ``*`` where the engine is jurisdiction-neutral."""

    regulator: str
    regime: Regime
    methodology_id: str
    """Distinguishes co-existing correct methods for the same metric."""

    return_family: str | None
    """``RETURN_FAMILIES`` value this metric is filed under, or None."""

    # -- effectivity ------------------------------------------------------
    effective_from: date
    effective_to: date | None = None

    # -- computation ------------------------------------------------------
    canonical_inputs: tuple[str, ...] = ()
    """Stable ``kind:name`` input identifiers (``fact:``, ``position:``,
    ``reference:``, ``run:``, ``live:``, ``param:``, ``curve:``).

    ``run:`` and ``live:`` are the platform's two tiers and are not
    interchangeable (ARCHITECTURE.md §3b): ``run:`` is a figure read off a sealed
    ``RegulatoryRun``, ``live:`` is a module's ``live_metrics`` payload, which the
    worker re-derives and which no filing may depend on."""

    policy_resolver: str = EXTERNAL_REGULATORY_VERIFICATION_REQUIRED
    """Dotted path of the function that resolves this metric's parameters."""

    calculation_engine: str = ""
    """``module.path:callable`` — import-checked by the registry test suite."""

    calculation_version: str = ""
    """The declared ``ENGINE_VERSION`` constant for that engine."""

    parameter_set: tuple[str, ...] = ()
    """Named governed parameter registers / codes this metric consumes."""

    authoritative_run_type: str | None = None
    """``RegulatoryRun.module`` that seals this figure, or None when the metric
    is computed on read (template evaluation / direct canonical query)."""

    # -- reporting --------------------------------------------------------
    reporting_mappings: tuple[str, ...] = ()
    """Return codes / template cells this figure feeds, e.g. ``BSD5A!E70``."""

    line_item_codes: tuple[str, ...] = ()
    """``section:line_code`` rows this authority's engine writes to
    ``regulatory_line_items``, for the NAMED lines only.

    A sealed run persists two things: metric results, and line items. The metric
    half had a completeness gate from 2026-08-22; this is the other half. Only
    the fixed vocabulary belongs here — ``ratio:car``, ``fx_var:portfolio_var``.
    Line codes keyed by the bank's own book (a GL category, a currency, a
    product, a tenor label) are not a vocabulary a registry can enumerate, and
    pretending otherwise would make this field a lie the first time a tenant
    loaded a new product. The gate measures that boundary rather than assuming
    it (``CodeEvidence.data_keyed_line_item_codes``).

    Declaring a line code here asserts **no new legal basis**: it records that
    the engine already carrying this authority also writes that line.
    """

    expected_tolerance: Decimal | None = None
    """Permitted absolute difference when a declared consumer reproduces this
    figure. ``Decimal("0")`` means exact. ``None`` means **no equivalence check
    is established yet** — an open item for WS-B/WS-C, not a pass."""

    # -- authority boundaries --------------------------------------------
    approved_alternate_methodologies: tuple[str, ...] = ()
    """``methodology_id`` values that also legitimately compute this metric."""

    divergence: MethodologyDivergence | None = None
    """Required whenever ``is_primary`` is False."""

    forbidden_alternative_sources: tuple[str, ...] = ()
    """Dotted paths / concepts that must NEVER supply this metric's value."""

    advisory_designation: AdvisoryDesignation = AdvisoryDesignation.FILED

    # -- governance -------------------------------------------------------
    authority_reference: str = EXTERNAL_REGULATORY_VERIFICATION_REQUIRED
    """The legal citation establishing this metric, or the sentinel."""

    instrument_in_force: bool = True
    """False when the cited instrument is published but has NOT commenced.

    A third governance state the registry could not express (2026-08-22 round 2).
    ``authority_reference`` answered one question — is there a citation — and
    :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED` answered its negative. But
    a real paragraph number in a document that is not yet law is neither: the
    nine Liquidity Monitoring Tools Directive entries cite ``paragraph 9`` of an
    **exposure draft** posted 19 February 2026 and effective 1 January 2027, and
    were designated ``FILED`` with ``requires_external_verification`` False, so
    no rule could see them.

    This flag does not decide whether filing on a draft is acceptable — the
    platform builds against four such drafts deliberately, and a bank preparing
    for commencement is a legitimate product. It decides that the choice cannot
    be silent: ``check_completeness`` requires every FILED methodology standing
    on an uncommenced instrument to be named with a reason, exactly as the
    sentinel register requires of an unestablished citation.
    """

    notes: str = ""
    is_primary: bool = True
    audit_findings: tuple[str, ...] = field(default_factory=tuple)
    """Forensic-audit findings attached to this entry."""

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("MetricAuthority.metric_id must be non-empty")
        if not self.methodology_id:
            raise ValueError("MetricAuthority.methodology_id must be non-empty")
        if not self.is_primary and self.divergence is None:
            raise ValueError(
                f"alternate methodology {self.metric_id}/{self.methodology_id} must document "
                "its divergence; a silent duplicate is a defect, not an alternate"
            )
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("MetricAuthority.effective_to precedes effective_from")

    @property
    def key(self) -> AuthorityKey:
        return AuthorityKey(self.metric_id, self.regime, self.methodology_id)

    @property
    def requires_external_verification(self) -> bool:
        """True when any governance field still carries the sentinel.

        Substring, not equality: several citations are partially established
        (e.g. the Basel IRRBB standard is known but BoG's prescribed shock set
        is not), and those must still surface as needing verification.
        """
        return any(
            EXTERNAL_REGULATORY_VERIFICATION_REQUIRED in value
            for value in (
                self.authority_reference,
                self.policy_resolver,
                self.calculation_version,
            )
        )

    @property
    def source_citation(self) -> str:
        """The platform's name for :attr:`authority_reference`.

        ``regulatory_parameter``, ``system_of_record`` and
        :mod:`app.domain.policy.resolver` all call a governed legal basis a
        ``source_citation``; the forensic re-audit uses that name too. This
        registry named the same thing ``authority_reference`` first, and it is
        a serialised key (``to_dict``) that provenance already writes, so it
        does not get renamed. This alias exists so the completeness rules and
        every reader can speak one vocabulary.
        """
        return self.authority_reference

    def missing_required_fields(self) -> tuple[str, ...]:
        """Required governance fields left blank. Empty tuple means complete.

        Blank is not the same as :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED`.
        The sentinel is an honest declaration that no legal basis is established
        in this repository; a blank field is an entry that never answered the
        question at all, which is what lets an unbacked figure look registered.
        """
        return tuple(
            name
            for name in REQUIRED_AUTHORITY_FIELDS
            if not str(getattr(self, name, "") or "").strip()
        )

    def is_effective_on(self, when: date) -> bool:
        if when < self.effective_from:
            return False
        return self.effective_to is None or when <= self.effective_to

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "metric_family": self.metric_family.value,
            "institution_class": self.institution_class.value,
            "jurisdiction": self.jurisdiction,
            "regulator": self.regulator,
            "regime": self.regime.value,
            "methodology_id": self.methodology_id,
            "return_family": self.return_family,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "canonical_inputs": list(self.canonical_inputs),
            "policy_resolver": self.policy_resolver,
            "calculation_engine": self.calculation_engine,
            "calculation_version": self.calculation_version,
            "parameter_set": list(self.parameter_set),
            "authoritative_run_type": self.authoritative_run_type,
            "reporting_mappings": list(self.reporting_mappings),
            "expected_tolerance": (
                str(self.expected_tolerance) if self.expected_tolerance is not None else None
            ),
            "approved_alternate_methodologies": list(self.approved_alternate_methodologies),
            "divergence": self.divergence.to_dict() if self.divergence else None,
            "forbidden_alternative_sources": list(self.forbidden_alternative_sources),
            "advisory_designation": self.advisory_designation.value,
            "authority_reference": self.authority_reference,
            "is_primary": self.is_primary,
            "notes": self.notes,
            "audit_findings": list(self.audit_findings),
        }


class DuplicateAuthorityError(ValueError):
    """Two authorities claimed the same (metric x regime x methodology)."""

    def __init__(self, key: AuthorityKey, existing: MetricAuthority) -> None:
        self.key: AuthorityKey = key
        self.existing: MetricAuthority = existing
        super().__init__(
            f"duplicate metric authority for {key}: already owned by "
            f"{existing.calculation_engine or '<unset engine>'}. Exactly one engine may own a "
            "metric under a regime+methodology. If this is a genuinely different method, give "
            "it its own methodology_id and a MethodologyDivergence."
        )


class UnknownMetricError(KeyError):
    """No authority registered for the requested metric."""


@dataclass(frozen=True, slots=True)
class CompletenessFailure:
    """One way the registry disagrees with the repository.

    ``rule`` is the machine-readable name of the invariant that broke,
    ``subject`` the entry / metric / methodology it broke on, and ``message``
    the sentence an engineer reads at 2am. Failures are data, not exceptions:
    :meth:`MetricAuthorityRegistry.check_completeness` returns all of them so
    the build reports the whole gap, not the first one.
    """

    rule: str
    subject: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.subject}: {self.message}"


@dataclass(frozen=True, slots=True)
class CodeEvidence:
    """What the repository actually does, read from the repository.

    Every field here MUST be derived by walking code — the persistence sites
    that write regulatory metric results, the live reporting registry object.
    The moment one of them becomes a list typed out beside the registry, the
    gate rots exactly the way the registry it guards rotted (forensic re-audit
    D-9), because a list of metrics only fails when a registration is removed;
    it can never fail when a computation is added.
    """

    computed_metrics: Mapping[str, str]
    """``metric_id -> file:line`` of the site that computes and persists it."""

    reporting_references: Mapping[tuple[str, str], str]
    """``(metric_id, methodology_id) -> the return that declares the pair``."""

    return_codes: frozenset[str]
    """Every return code the reporting registry defines."""

    return_families: frozenset[str]
    """Every return family the reporting registry defines."""

    acknowledged_pending_filed: Mapping[str, str]
    """``methodology_id -> why its citation is not established``, for
    methodologies that file on :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED`.
    An acknowledgement is a disclosure, never a substitute for a citation."""

    # -- the three planes the first gate could not see (2026-08-22, round 2) --
    #
    # Defaulted so a caller that supplies only the filed-metric plane still gets
    # every rule that plane supports. An empty plane disables its own rules and
    # nothing else; the test module pins that each is non-vacuous, because a
    # silently empty plane is how a gate becomes decoration.

    filed_line_item_codes: Mapping[str, str] = field(default_factory=dict)
    """``section:line_code -> file:line`` for NAMED line items a sealed run
    persists. The other half of what a filing run writes."""

    data_keyed_line_item_codes: Mapping[str, str] = field(default_factory=dict)
    """``section:expression -> file:line`` for line codes built from the bank's
    own book. Recorded so the enumerable/non-enumerable boundary is measured,
    never asserted. No rule fires on these."""

    live_module_metrics: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """``module path -> {live metric key: file:line}`` for every figure a
    ``compute_live`` upserts into ``live_metrics``. Not a filed plane — but it
    is the plane a treasurer reads daily, and it was invisible."""

    acknowledged_filed_on_draft: Mapping[str, str] = field(default_factory=dict)
    """``methodology_id -> why filing on an uncommenced instrument is intended``,
    for methodologies with ``instrument_in_force=False``. A disclosure, never a
    finding that the instrument is in force."""

    sdi_read_side_metrics: Mapping[str, str] = field(default_factory=dict)
    """``code -> file:line`` for SDI read-side figures served with a threshold
    and a status. No ``RegulatoryRun`` is involved; the judgment is the same."""


class MetricAuthorityRegistry:
    """Append-only registry with a hard uniqueness guarantee."""

    def __init__(self) -> None:
        self._by_key: dict[AuthorityKey, MetricAuthority] = {}

    def register(self, authority: MetricAuthority) -> MetricAuthority:
        key = authority.key
        existing = self._by_key.get(key)
        if existing is not None:
            raise DuplicateAuthorityError(key, existing)
        self._by_key[key] = authority
        return authority

    def register_all(self, authorities: Iterable[MetricAuthority]) -> None:
        for authority in authorities:
            self.register(authority)

    # -- lookup ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[MetricAuthority]:
        return iter(self._by_key.values())

    def __contains__(self, metric_id: object) -> bool:
        return any(entry.metric_id == metric_id for entry in self._by_key.values())

    @property
    def keys(self) -> tuple[AuthorityKey, ...]:
        return tuple(self._by_key)

    def all(self) -> tuple[MetricAuthority, ...]:
        return tuple(self._by_key.values())

    def get(self, metric_id: str, *, regime: Regime, methodology_id: str) -> MetricAuthority:
        try:
            return self._by_key[AuthorityKey(metric_id, regime, methodology_id)]
        except KeyError as exc:
            raise UnknownMetricError(
                f"no registered authority for {metric_id}@{regime.value}/{methodology_id}"
            ) from exc

    def for_metric(self, metric_id: str) -> tuple[MetricAuthority, ...]:
        found = tuple(e for e in self._by_key.values() if e.metric_id == metric_id)
        if not found:
            raise UnknownMetricError(f"no registered authority for metric {metric_id!r}")
        return found

    def primary_for(
        self, metric_id: str, *, regime: Regime, on: date | None = None
    ) -> MetricAuthority:
        """The single primary authority for a metric under one regime."""
        candidates = [
            entry
            for entry in self._by_key.values()
            if entry.metric_id == metric_id and entry.regime is regime and entry.is_primary
        ]
        if on is not None:
            candidates = [entry for entry in candidates if entry.is_effective_on(on)]
        if not candidates:
            raise UnknownMetricError(
                f"no primary authority for {metric_id!r} under regime {regime.value}"
            )
        if len(candidates) > 1:  # pragma: no cover - register() makes this unreachable
            raise DuplicateAuthorityError(candidates[0].key, candidates[1])
        return candidates[0]

    def for_family(self, family: MetricFamily) -> tuple[MetricAuthority, ...]:
        return tuple(e for e in self._by_key.values() if e.metric_family is family)

    def for_regime(self, regime: Regime) -> tuple[MetricAuthority, ...]:
        return tuple(e for e in self._by_key.values() if e.regime is regime)

    def for_institution_class(self, klass: InstitutionClass) -> tuple[MetricAuthority, ...]:
        return tuple(
            e
            for e in self._by_key.values()
            if e.institution_class is klass or e.institution_class is InstitutionClass.ALL
        )

    def filable(self) -> tuple[MetricAuthority, ...]:
        return tuple(
            e for e in self._by_key.values() if e.advisory_designation is AdvisoryDesignation.FILED
        )

    def multi_authority_metrics(self) -> Mapping[str, tuple[MetricAuthority, ...]]:
        """Metrics with more than one registered authority.

        The registry's headline output for WS-B/C/D: every entry here is a place
        where the SAME NAME is produced by more than one declared method, so a
        consumer must state which one it means.
        """
        grouped: dict[str, list[MetricAuthority]] = {}
        for entry in self._by_key.values():
            grouped.setdefault(entry.metric_id, []).append(entry)
        return {k: tuple(v) for k, v in sorted(grouped.items()) if len(v) > 1}

    def unresolved_divergences(self) -> tuple[MetricAuthority, ...]:
        """Alternates the audit flagged as defects, not legitimate methods."""
        return tuple(
            e
            for e in self._by_key.values()
            if e.divergence is not None and not e.divergence.is_accepted
        )

    def requiring_external_verification(self) -> tuple[MetricAuthority, ...]:
        return tuple(e for e in self._by_key.values() if e.requires_external_verification)

    def engines(self) -> tuple[str, ...]:
        return tuple(
            sorted({e.calculation_engine for e in self._by_key.values() if e.calculation_engine})
        )

    def counts_by_family(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for entry in self._by_key.values():
            counts[entry.metric_family.value] = counts.get(entry.metric_family.value, 0) + 1
        return dict(sorted(counts.items()))

    # -- completeness gate -------------------------------------------------

    def check_completeness(self, evidence: CodeEvidence) -> tuple[CompletenessFailure, ...]:
        """Every way this registry can be incomplete or incoherent, measured.

        Forensic re-audit D-9: ``register()`` raises only on an
        :class:`AuthorityKey` collision, so the registry could disagree with the
        repository in every other direction at once — a metric computed and
        filed with no authority, a return declaring a ``methodology_id`` that
        exists nowhere (D-10), an entry with no citation, a bank reaching an
        Act 930 authority. Nothing measured that.

        This method is the measurement, and it is pure: every fact about the
        code arrives in ``evidence``. The caller derives those facts by reading
        the repository — never from a list maintained beside this file, which
        is precisely how the registry itself came to be a passive catalogue.

        Returns every failure, not the first: a partial answer to "is the
        registry complete" is what the audit found.
        """
        failures: list[CompletenessFailure] = []
        failures.extend(self._unbacked_computed_metrics(evidence))
        failures.extend(self._dangling_reporting_references(evidence))
        failures.extend(self._incomplete_entries())
        failures.extend(self._regime_boundary_breaches())
        failures.extend(self._unresolvable_reporting_targets(evidence))
        failures.extend(self._unacknowledged_pending_filed(evidence))
        failures.extend(self._unbacked_filed_line_items(evidence))
        failures.extend(self._unregistered_calculation_modules(evidence))
        failures.extend(self._undisclosed_draft_instruments(evidence))
        return tuple(failures)

    def _undisclosed_draft_instruments(
        self, evidence: CodeEvidence
    ) -> Iterator[CompletenessFailure]:
        """A FILED figure standing on an instrument that has not commenced.

        Distinct from the sentinel rule and NOT a substitute for it: an entry can
        carry a perfectly good paragraph number in a document that is not yet
        law, which is exactly the Liquidity Monitoring Tools Directive's position
        and exactly why nothing could see it. Filing ahead of commencement may be
        the intended product - it may not be an accident nobody recorded.
        """
        drafts = {
            entry.methodology_id
            for entry in self._by_key.values()
            if entry.advisory_designation is AdvisoryDesignation.FILED
            and not entry.instrument_in_force
        }
        for methodology_id in sorted(drafts - set(evidence.acknowledged_filed_on_draft)):
            citation = next(
                entry.authority_reference
                for entry in self._by_key.values()
                if entry.methodology_id == methodology_id
            )
            yield CompletenessFailure(
                rule="undisclosed_draft_instrument",
                subject=methodology_id,
                message=(
                    f"methodology {methodology_id!r} is designated FILED on {citation!r}, an "
                    "instrument this registry records as published but NOT commenced. The "
                    "citation is real, so the pending-verification register cannot see it. "
                    "Name it in the draft-instrument register with the reason filing ahead "
                    "of commencement is intended, or stop designating it FILED."
                ),
            )
        for methodology_id in sorted(set(evidence.acknowledged_filed_on_draft) - drafts):
            yield CompletenessFailure(
                rule="stale_draft_acknowledgement",
                subject=methodology_id,
                message=(
                    f"methodology {methodology_id!r} is acknowledged as filing on an "
                    "uncommenced instrument, but no FILED entry under it still declares "
                    "instrument_in_force=False. Remove the acknowledgement, or the register "
                    "stops meaning what it says the day an instrument commences."
                ),
            )

    def _unbacked_filed_line_items(self, evidence: CodeEvidence) -> Iterator[CompletenessFailure]:
        """A NAMED line filed into a sealed run that no authority claims.

        ``RegulatoryMetricResult`` rows are half of what a run persists;
        ``regulatory_line_items`` is the other half, and it carries figures no
        metric row does — the operational-risk BIA charge, the FX VaR
        decomposition, the SDI's prescribed percentage-of-credit-RWA charges.
        Their codes are chosen inside the engines rather than at the persistence
        site, which is exactly why the first gate could not see them.
        """
        claimed = {code for entry in self._by_key.values() for code in entry.line_item_codes}
        for code, site in sorted(evidence.filed_line_item_codes.items()):
            if code in claimed:
                continue
            yield CompletenessFailure(
                rule="unbacked_filed_line_item",
                subject=code,
                message=(
                    f"{site}: line {code!r} is written into a sealed regulatory run and no "
                    "authority declares it in line_item_codes. Same rule as a metric "
                    "result: an engine states what authorises the line, or it stops "
                    "filing it. Declaring it asserts no new legal basis - only that the "
                    "authority already covering this engine also writes this line."
                ),
            )

    def _unregistered_calculation_modules(
        self, evidence: CodeEvidence
    ) -> Iterator[CompletenessFailure]:
        """A module that publishes live figures with no authority naming it.

        The live plane is not filed, so "every live key needs an authority" is
        the wrong rule — a live payload legitimately carries labels, scenario
        codes and echoed parameters alongside its metrics, and curating a
        per-key allow-list would rot into a rubber stamp within two releases.

        What is NOT a judgment call is a module that publishes figures of which
        **not one** is a registered metric and whose engine module nothing
        points at: that engine is absent from the registry entirely. It is the
        largest form of the defect D-9 describes, it needs no allow-list to
        detect, and the count of unowned keys in the message says how much of
        the module is dark.
        """
        engine_modules = {
            entry.calculation_engine.split(":", 1)[0]
            for entry in self._by_key.values()
            if entry.calculation_engine
        }
        registered = {entry.metric_id for entry in self._by_key.values()}
        for module, metrics in sorted(evidence.live_module_metrics.items()):
            dotted = module.removesuffix(".py").replace("/", ".")
            if dotted in engine_modules or (set(metrics) & registered):
                continue
            yield CompletenessFailure(
                rule="unregistered_calculation_module",
                subject=module,
                message=(
                    f"{module} publishes {len(metrics)} figures to the live plane, not "
                    "one of which is a registered metric, and no authority names it as a "
                    "calculation_engine. Nothing in this registry declares under what "
                    "basis any figure this module produces exists. Published: "
                    f"{sorted(metrics)}."
                ),
            )

    def _unbacked_computed_metrics(self, evidence: CodeEvidence) -> Iterator[CompletenessFailure]:
        """A number the code computes and persists that no authority owns."""
        registered = {entry.metric_id for entry in self._by_key.values()}
        for metric_id, site in sorted(evidence.computed_metrics.items()):
            if metric_id in registered:
                continue
            yield CompletenessFailure(
                rule="unbacked_computed_metric",
                subject=metric_id,
                message=(
                    f"{site}: {metric_id!r} is computed and persisted as a regulatory "
                    "metric result, and no authority in this registry owns it. Either an "
                    "engine states its authority or it stops producing a filed figure. Do "
                    "not resolve this by inventing an entry: if no legal basis exists, the "
                    "finding is the computation, not the missing row."
                ),
            )

    def _dangling_reporting_references(
        self, evidence: CodeEvidence
    ) -> Iterator[CompletenessFailure]:
        """A return declaring a (metric, methodology) pair that resolves nowhere."""
        for (metric_id, methodology_id), source in sorted(evidence.reporting_references.items()):
            candidates = tuple(e for e in self._by_key.values() if e.metric_id == metric_id)
            if any(entry.methodology_id == methodology_id for entry in candidates):
                continue
            known = sorted({entry.methodology_id for entry in candidates})
            yield CompletenessFailure(
                rule="dangling_methodology_reference",
                subject=f"{metric_id}/{methodology_id}",
                message=(
                    f"{source} declares methodology {methodology_id!r} for metric "
                    f"{metric_id!r}, which no authority carries. The declaration is a "
                    "no-op: the filed package records registry_status 'not_registered' "
                    "and discloses no divergence at all. Registered methodologies for "
                    f"this metric: {known or 'none - the metric itself is unregistered'}."
                ),
            )

    def _incomplete_entries(self) -> Iterator[CompletenessFailure]:
        for entry in self._by_key.values():
            missing = entry.missing_required_fields()
            if not missing:
                continue
            yield CompletenessFailure(
                rule="missing_required_field",
                subject=str(entry.key),
                message=(
                    f"authority {entry.key} leaves {list(missing)} blank. A field may say "
                    f"{EXTERNAL_REGULATORY_VERIFICATION_REQUIRED} - it may not say nothing."
                ),
            )

    def _regime_boundary_breaches(self) -> Iterator[CompletenessFailure]:
        """The bank/CRD and SDI/Act 930 regimes sharing or inheriting authority."""
        for entry in self._by_key.values():
            bound = CLASS_SPECIFIC_REGIMES.get(entry.regime)
            if bound is None or entry.institution_class is bound:
                continue
            reached = (
                "which for_institution_class() hands to BOTH classes"
                if entry.institution_class is InstitutionClass.ALL
                else "which is the other regime's class"
            )
            yield CompletenessFailure(
                rule="regime_class_mismatch",
                subject=str(entry.key),
                message=(
                    f"authority {entry.key} is registered under regime "
                    f"{entry.regime.value!r}, which binds {bound.value!r} institutions "
                    f"only, but declares institution_class {entry.institution_class.value!r} "
                    f"- {reached}. Engine: {entry.calculation_engine}."
                ),
            )

        by_methodology: dict[str, set[Regime]] = {}
        by_citation: dict[str, set[Regime]] = {}
        for entry in self._by_key.values():
            by_methodology.setdefault(entry.methodology_id, set()).add(entry.regime)
            if entry.regime in CLASS_SPECIFIC_REGIMES:
                by_citation.setdefault(entry.authority_reference, set()).add(entry.regime)
        for methodology_id, regimes in sorted(by_methodology.items()):
            if len(regimes) > 1:
                yield CompletenessFailure(
                    rule="methodology_shared_across_regimes",
                    subject=methodology_id,
                    message=(
                        f"methodology {methodology_id!r} is claimed by regimes "
                        f"{sorted(r.value for r in regimes)}. A methodology is a method "
                        "under one law; two regimes sharing one id means a consumer that "
                        "resolves by methodology cannot tell which law it got."
                    ),
                )
        for citation, regimes in sorted(by_citation.items()):
            if len(regimes) > 1:
                yield CompletenessFailure(
                    rule="citation_shared_across_regimes",
                    subject=citation,
                    message=(
                        f"citation {citation!r} backs entries in regimes "
                        f"{sorted(r.value for r in regimes)}. The CRD does not authorise "
                        "an Act 930 figure and Act 930 does not authorise a CRD figure; "
                        "one citation covering both is an inherited authority."
                    ),
                )

    def _unresolvable_reporting_targets(
        self, evidence: CodeEvidence
    ) -> Iterator[CompletenessFailure]:
        """``reporting_mappings`` / ``return_family`` naming a return that is gone."""
        for entry in self._by_key.values():
            for mapping in entry.reporting_mappings:
                code = mapping.split("!", 1)[0]
                if code in evidence.return_codes:
                    continue
                yield CompletenessFailure(
                    rule="dangling_reporting_mapping",
                    subject=f"{entry.key} -> {mapping}",
                    message=(
                        f"authority {entry.key} claims it feeds return {code!r}, which the "
                        "reporting registry does not define. A mapping to a return that "
                        "does not exist is the same rot as a methodology that does not."
                    ),
                )
            if entry.return_family and entry.return_family not in evidence.return_families:
                yield CompletenessFailure(
                    rule="dangling_return_family",
                    subject=f"{entry.key} -> {entry.return_family}",
                    message=(
                        f"authority {entry.key} declares return_family "
                        f"{entry.return_family!r}, which no registered return uses."
                    ),
                )

    def _unacknowledged_pending_filed(
        self, evidence: CodeEvidence
    ) -> Iterator[CompletenessFailure]:
        """A FILED figure standing on an unverified citation, unnoticed.

        "No authority" and "authority pending external verification" are
        different states and the gate must not conflate them. The sentinel is
        allowed - the platform files figures whose BoG basis this repository
        cannot establish, and saying so is the honest move. What is not allowed
        is for that to be invisible: every methodology filing on the sentinel is
        named in ``evidence.acknowledged_pending_filed`` with its reason, so a
        NEW one fails the build and a RESOLVED one cannot leave a stale
        acknowledgement behind.
        """
        pending = {
            entry.methodology_id
            for entry in self._by_key.values()
            if entry.advisory_designation is AdvisoryDesignation.FILED
            and entry.requires_external_verification
        }
        for methodology_id in sorted(pending - set(evidence.acknowledged_pending_filed)):
            metrics = sorted(
                entry.metric_id
                for entry in self._by_key.values()
                if entry.methodology_id == methodology_id
                and entry.advisory_designation is AdvisoryDesignation.FILED
                and entry.requires_external_verification
            )
            yield CompletenessFailure(
                rule="unacknowledged_pending_filed",
                subject=methodology_id,
                message=(
                    f"methodology {methodology_id!r} files {metrics} on a citation carrying "
                    f"{EXTERNAL_REGULATORY_VERIFICATION_REQUIRED}. Filing on an unverified "
                    "basis may be the honest state of the world, but it may not be a silent "
                    "one: name it in the acknowledged register with the reason the basis is "
                    "unestablished, or stop designating these metrics FILED."
                ),
            )
        for methodology_id in sorted(set(evidence.acknowledged_pending_filed) - pending):
            yield CompletenessFailure(
                rule="stale_pending_acknowledgement",
                subject=methodology_id,
                message=(
                    f"methodology {methodology_id!r} is acknowledged as filing on an "
                    "unverified citation, but no FILED entry under it needs external "
                    "verification any more. Remove the acknowledgement so the register "
                    "keeps meaning what it says."
                ),
            )


# =========================================================================
# Registered authorities — discovered from this repository, never invented.
# =========================================================================

_BOG = "BOG"
_GH = "GH"

#: Platform epoch. These engines carry no effective-dated versioning today;
#: real effective dating is WS-E's policy resolver (primitive P2). Using one
#: honest epoch is better than inventing per-metric commencement dates.
_EPOCH = date(2026, 1, 1)

_PARAMS_TENANT = "app.services.params:get_active_params"
_PARAMS_CONTROL = "app.services.regulatory_parameters:resolve"

# Both bumped to v2.0.0 on 2026-08-22 (forensic re-audit D-5): each engine
# changed methodology in a way that moves a filed figure over unchanged inputs.
# The declaring constants carry the reasoning and the versioning rule
# (``app.services.regulatory_capital.ENGINE_VERSION`` and its liquidity peer);
# these must track them, and a registry test asserts they do.
_CAPITAL_VERSION = "regulatory-capital-v2.0.0"
_LIQ_VERSION = "regulatory-liquidity-v2.0.0"
_IRR_VERSION = "regulatory-irr-v1.0.0"
_FX_VERSION = "regulatory-fx-v1.0.0"

#: Not the pending-verification sentinel: a stated negative. The Capital
#: Requirements Directive was searched in full and contains no value-at-risk
#: provision (backend/docs/bog_parameter_sources.md section 7, "VERIFIED
#: (negative)"), so there is nothing outstanding for an external body to confirm.
_NO_VAR_AUTHORITY = (
    "No external regulatory authority - the Capital Requirements Directive mandates the "
    "Standardised Method at paragraph 310 and contains no value-at-risk provision "
    "(verified negative, full-text search). Internal market-risk management measure."
)
_FTP_VERSION = "regulatory-ftp-v1.0.0"
_FORECAST_VERSION = "regulatory-forecasting-v1.0.0"
_STRESS_VERSION = "enterprise-stress-v1.0.0"
_REVERSE_VERSION = "reverse-stress-v1.0.0"

#: The case-scoped ("legacy/advisory") plane. The forensic audit found its
#: liquidity/capital outputs share NAMES with regulatory metrics while being
#: mathematically different. Nothing regulatory may ever read them.
_FORBID_CASE_PLANE: tuple[str, ...] = (
    "app.services.calculations:calculate_forecast",
    "app.services.liquidity:calculate_metrics",
    "app.services.capital:_indicator",
    "app.models.calculation:CalculationRun",
    "app.models.calculation:CalculationForecastPeriod",
    "app.models.capital:CapitalProjection",
)

_FORBID_CLIENT = ("dashboard client-side arithmetic",)

_AUDIT_FORECAST_CAR = (
    "forensic_calculation_audit_2026-08-21.md - MATERIAL ARCHITECTURE FINDING: "
    "TWO PARALLEL FORECASTING SYSTEMS",
    "FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md - section 5, "
    "'Forecast year-0 CAR' classified Dangerous semantic duplication / HIGH",
)


def _capital_run(  # noqa: PLR0913 - keyword-only entry builder, not a call site
    metric_id: str,
    *,
    engine: str,
    inputs: Sequence[str],
    reporting: Sequence[str] = (),
    notes: str = "",
    designation: AdvisoryDesignation = AdvisoryDesignation.FILED,
    alternates: Sequence[str] = (),
    tolerance: Decimal | None = Decimal("0"),
    parameters: Sequence[str] = ("ParamRiskWeight", "ParamCapitalThreshold"),
    lines: Sequence[str] = (),
) -> MetricAuthority:
    """Universal-bank CRD/Basel capital metric sealed by ``RegulatoryRun(capital)``."""
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.CAPITAL,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.CRD_BASEL,
        methodology_id="crd_basel_capital_run",
        return_family="capital",
        effective_from=_EPOCH,
        canonical_inputs=tuple(inputs),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine=engine,
        calculation_version=_CAPITAL_VERSION,
        parameter_set=tuple(parameters),
        authoritative_run_type="capital",
        reporting_mappings=tuple(reporting),
        line_item_codes=tuple(lines),
        expected_tolerance=tolerance,
        approved_alternate_methodologies=tuple(alternates),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=designation,
        authority_reference="Basel III / BoG Capital Requirement Directive (CRD)",
        notes=notes,
    )


def _liquidity_run(  # noqa: PLR0913 - keyword-only entry builder, not a call site
    metric_id: str,
    *,
    engine: str,
    inputs: Sequence[str],
    reporting: Sequence[str] = ("LCR-NSFR",),
    notes: str = "",
    alternates: Sequence[str] = (),
    parameters: Sequence[str] = ("ParamLcrRunoffRate", "ParamNsfrWeight", "ParamCapitalThreshold"),
    lines: Sequence[str] = (),
) -> MetricAuthority:
    """Universal-bank Basel liquidity metric sealed by ``RegulatoryRun(liquidity)``."""
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.LIQUIDITY,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.CRD_BASEL,
        methodology_id="basel_bog_liquidity_run",
        return_family="liquidity",
        effective_from=_EPOCH,
        canonical_inputs=tuple(inputs),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine=engine,
        calculation_version=_LIQ_VERSION,
        parameter_set=tuple(parameters),
        authoritative_run_type="liquidity",
        reporting_mappings=tuple(reporting),
        line_item_codes=tuple(lines),
        expected_tolerance=Decimal("0"),
        approved_alternate_methodologies=tuple(alternates),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.FILED,
        authority_reference="Basel III LCR/NSFR as adopted by BoG (LCR-NSFR return)",
        notes=notes,
    )


def _irr(
    metric_id: str, *, engine: str, notes: str = "", lines: Sequence[str] = ()
) -> MetricAuthority:
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.IRRBB,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.CRD_BASEL,
        methodology_id="basel_irrbb_run",
        return_family="irrbb",
        effective_from=_EPOCH,
        canonical_inputs=("fact:irr_position", "curve:discount", "run:capital.tier1_capital"),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine=engine,
        calculation_version=_IRR_VERSION,
        parameter_set=("ParamStressShock", "ParamCapitalThreshold"),
        authoritative_run_type="irr",
        reporting_mappings=("IRRBB-PILOT",),
        line_item_codes=tuple(lines),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.FILED,
        authority_reference=(
            "Basel IRRBB standards; BoG IRRBB-PILOT return "
            f"({EXTERNAL_REGULATORY_VERIFICATION_REQUIRED} for BoG-specific shock set)"
        ),
        # The BoG instrument behind these figures - the Guideline on the
        # Management and Measurement of Interest Rate Risk in the Banking Book,
        # 2026 - is an exposure draft: posted 19 February 2026, effective
        # 1 January 2027 at P9, comment window closed 30 June 2026 with no final
        # version published (docs/bog_parameter_sources.md).
        instrument_in_force=False,
        notes=notes,
    )


def _fx(
    metric_id: str,
    *,
    engine: str,
    notes: str = "",
    lines: Sequence[str] = (),
    management_measure: str = "",
) -> MetricAuthority:
    """One FX authority.

    The default is the net-open-position case: FILED, on a citation this
    repository has not established (the pending-verification sentinel).

    ``management_measure`` inverts both at once, and only together, because for
    these entries they are one statement: it is the citation recording that no
    external authority exists, and an entry saying that cannot also be filed.
    See the ``var_99_1d_ghs`` entry for the reasoning.
    """
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.FX,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.CRD_BASEL,
        methodology_id="bog_fx_nop_run",
        return_family="fx",
        effective_from=_EPOCH,
        canonical_inputs=(
            "fact:fx_position",
            "reference:fx_rate_history",
            "run:capital.tier1_capital",
        ),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine=engine,
        calculation_version=_FX_VERSION,
        parameter_set=("ParamCapitalThreshold", "ParamStressShock"),
        authoritative_run_type="fx",
        reporting_mappings=("FX-NOP", "BSD13", "DBK-DAILY"),
        line_item_codes=tuple(lines),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=(
            AdvisoryDesignation.SUPERVISORY_MONITORING
            if management_measure
            else AdvisoryDesignation.FILED
        ),
        authority_reference=management_measure or EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
        notes=notes,
    )


def _ftp(
    metric_id: str, *, engine: str, notes: str = "", forbidden: Sequence[str] = ()
) -> MetricAuthority:
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.FTP,
        institution_class=InstitutionClass.BANK,
        jurisdiction="*",
        regulator="internal",
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="aequoros_ftp_run",
        return_family=None,
        effective_from=_EPOCH,
        canonical_inputs=("fact:ftp_product", "curve:ftp", "fact:nmd_segment"),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine=engine,
        calculation_version=_FTP_VERSION,
        parameter_set=("ParamCapitalThreshold", "ParamStressShock"),
        authoritative_run_type="ftp",
        reporting_mappings=(),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=tuple(forbidden) + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.ADVISORY_ONLY,
        authority_reference="Internal ALCO methodology - no external regulatory authority",
        notes=notes,
    )


def _forecast(metric_id: str, *, notes: str = "") -> MetricAuthority:
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.FORECAST,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="bank_forecast_projection_run",
        return_family="icaap_stress",
        effective_from=_EPOCH,
        canonical_inputs=("fact:bank_facts_v2", "param:forecast_assumptions"),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine="app.domain.forecasting.engine:project",
        calculation_version=_FORECAST_VERSION,
        parameter_set=(
            "ParamLcrRunoffRate",
            "ParamNsfrWeight",
            "ParamRiskWeight",
            "ParamCapitalThreshold",
        ),
        authoritative_run_type="forecast",
        reporting_mappings=("ICAAP-STRESS", "STRESS-PACK"),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
        authority_reference=(
            "ICAAP capital-plan projection; BoG ICAAP directive "
            f"({EXTERNAL_REGULATORY_VERIFICATION_REQUIRED} for a prescribed projection method)"
        ),
        notes=notes,
        audit_findings=(
            "Audit: the forecast engine is NOT regime-aware - Basel ratios are projected for "
            "SDI tenants too (architecture audit section 6, 'Open product/architecture gap').",
        ),
    )


REGISTRY = MetricAuthorityRegistry()

# --- CAPITAL: universal bank, CRD/Basel, RegulatoryRun(capital) -----------

REGISTRY.register_all(
    [
        _capital_run(
            "car_pct",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=(
                "fact:capital_component",
                "fact:credit_exposure",
                "fact:obs_exposure",
                "fact:gross_income",
                "fact:fx_net_open_position",
            ),
            reporting=("CAR-RWA", "BSD5A", "BSD5B"),
            alternates=(
                "bog_bsd5a_form_ratio",
                "act930_s29_nof_rwa",
                "bank_forecast_projection_path",
            ),
            notes="total regulatory capital / total RWA x 100.",
            lines=("ratio:car",),
        ),
        _capital_run(
            "tier1_ratio_pct",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component", "fact:credit_exposure"),
            reporting=("CAR-RWA",),
            lines=("ratio:tier1_ratio",),
        ),
        _capital_run(
            "cet1_ratio_pct",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component", "fact:credit_exposure"),
            reporting=("CAR-RWA",),
            lines=("ratio:cet1_ratio",),
        ),
        _capital_run(
            "leverage_ratio_pct",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component", "fact:balance_sheet", "fact:obs_exposure"),
            reporting=("CAR-RWA",),
            notes="Tier 1 / total leverage exposure x 100.",
            lines=("ratio:leverage_ratio",),
        ),
        _capital_run(
            "total_capital_ghs",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component",),
            reporting=("CAR-RWA", "BSD5A"),
        ),
        _capital_run(
            "cet1_capital",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component",),
            reporting=("CAR-RWA", "BSD5A"),
            notes=(
                "CapitalRatiosResult.cet1_capital. NOT a RegulatoryRun.metrics key - it "
                "persists as RegulatoryLineItem(section='capital_component')."
            ),
        ),
        _capital_run(
            "tier1_capital",
            engine="app.domain.capital.engine:tier1_capital",
            inputs=("fact:capital_component",),
            reporting=("CAR-RWA", "BSD5A!E10"),
            notes=(
                "Also consumed by the IRR and FX engines (metrics key 'tier1_ghs' on those "
                "runs). Persists as RegulatoryLineItem(section='capital_component')."
            ),
        ),
        _capital_run(
            "tier2_capital",
            engine="app.domain.capital.engine:compute_capital_ratios",
            inputs=("fact:capital_component", "fact:general_provisions"),
            reporting=("CAR-RWA", "BSD5A"),
            notes=(
                "General provisions are capped at tier2_gp_cap_pct_credit_rwa of credit RWA. "
                "Persists as RegulatoryLineItem, not a metrics key."
            ),
        ),
        _capital_run(
            "total_rwa_ghs",
            engine="app.domain.capital.engine:compute_rwa",
            inputs=(
                "fact:credit_exposure",
                "fact:obs_exposure",
                "fact:fx_net_open_position",
                "fact:gross_income",
            ),
            reporting=("CAR-RWA", "BSD5A!E69"),
            alternates=("act930_s29_nof_rwa",),
        ),
        _capital_run(
            "credit_rwa_ghs",
            engine="app.domain.capital.engine:compute_rwa",
            inputs=("fact:credit_exposure", "fact:obs_exposure", "fact:crm_collateral"),
            reporting=("CAR-RWA",),
            parameters=("ParamRiskWeight", "ParamCrmHaircut"),
            # The residual bucket _credit_line_items writes for exposures no
            # governed risk-weight class claims; the code is the platform's,
            # not the bank's, which is why it is enumerable at all.
            lines=("credit_rwa:other_assets",),
        ),
        _capital_run(
            "market_rwa_ghs",
            engine="app.domain.capital.engine:compute_rwa",
            inputs=("fact:fx_net_open_position",),
            reporting=("CAR-RWA",),
            notes="FX charge x RWA multiplier. BSD5A instead uses 50% of NOP (see divergence).",
            lines=(
                "market_rwa:net_long_fx",
                "market_rwa:net_short_fx",
                "market_rwa:fx_charge",
                "market_rwa:fx_rwa",
            ),
        ),
        _capital_run(
            "operational_rwa_ghs",
            engine="app.domain.capital.engine:compute_rwa",
            inputs=("fact:gross_income",),
            reporting=("CAR-RWA",),
            notes=(
                "Basic Indicator Approach: BIA alpha x 3-year average gross income x RWA "
                "multiplier. BSD5A instead uses 100% of the 3-year average."
            ),
            lines=("operational_rwa:bia_charge", "operational_rwa:operational_rwa"),
        ),
    ]
)

# --- CAPITAL: alternate methodologies (declared, not silent) --------------

REGISTRY.register(
    MetricAuthority(
        metric_id="car_pct",
        metric_family=MetricFamily.CAPITAL,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.CRD_BASEL,
        methodology_id="bog_bsd5a_form_ratio",
        return_family="bsd",
        effective_from=_EPOCH,
        canonical_inputs=(
            "fact:capital_component",
            "fact:balance_sheet",
            "run:capital.line_items",
            "cell:BSD5A!E25",
            "cell:BSD5A!E69",
        ),
        policy_resolver="app.services.regulatory_reporting.bog_forms.formulas",
        calculation_engine="app.services.regulatory_reporting.bog_forms.engine:compute_form",
        calculation_version="bog_form/template-formula-evaluator",
        parameter_set=("bog_bsd5a_layout", "bog_bsd5a_linemap"),
        authoritative_run_type=None,
        reporting_mappings=("BSD5A!E70", "BSD5B!D74"),
        expected_tolerance=None,
        forbidden_alternative_sources=_FORBID_CASE_PLANE,
        advisory_designation=AdvisoryDesignation.FILED,
        authority_reference="Bank of Ghana BSD5A Capital Adequacy Return, cell E70 (=E25/E69)",
        is_primary=False,
        divergence=MethodologyDivergence(
            versus_methodology_id="crd_basel_capital_run",
            authority_reference="BoG BSD5A workbook formula E70 = E25/E69",
            reason=(
                "Same NOP and gross income, different add-on rules: BoG takes 50% of NOP and "
                "100% of the 3-year average gross income; the engine takes the FX charge x RWA "
                "multiplier and the BIA charge x RWA multiplier. Credit is weighted by BoG's "
                "printed classes rather than the standardised weights."
            ),
            direction="either",
            reconciliation_rule=(
                "NOT reconciled by equality. The add-ons reconcile component-wise: "
                "market_rwa_ghs == E67 x 2 x fx_charge_pct x rwa_multiplier and "
                "operational_rwa_ghs == E68 x bia_alpha_pct x rwa_multiplier."
            ),
            resolution_status=ACCEPTED_BY_AUTHORITY,
            evidence=(
                "backend/tests/services/bog_forms/test_bsd5.py - "
                '\'assert not _close(e["E70"] * 100, car_pct, "0.5")  '
                "# by construction, not by accident'",
            ),
        ),
        notes=(
            "The BoG template is the authority for what BSD5A files. Never re-implement E70 "
            "and never bind it as an input cell."
        ),
        audit_findings=(
            "forensic_calculation_audit_2026-08-21.md - CRITICAL AUDIT FINDINGS 2: "
            "'BoG Formulas are Authoritative, Never Re-implemented'.",
        ),
    )
)

REGISTRY.register(
    MetricAuthority(
        metric_id="car_pct",
        metric_family=MetricFamily.CAPITAL,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="bank_forecast_projection_path",
        return_family=None,
        effective_from=_EPOCH,
        canonical_inputs=("fact:bank_facts_v2", "param:forecast_assumptions"),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine="app.domain.forecasting.engine:project",
        calculation_version=_FORECAST_VERSION,
        parameter_set=("ParamRiskWeight", "ParamCapitalThreshold"),
        authoritative_run_type="forecast",
        reporting_mappings=(),
        expected_tolerance=None,
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.ADVISORY_ONLY,
        authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
        is_primary=False,
        divergence=MethodologyDivergence(
            versus_methodology_id="crd_basel_capital_run",
            authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            reason=(
                "The forecast snapshot's fact scope excludes ecl_exposure, while the capital "
                "run may apply modeled IFRS 9 ECL. Year 0 of the projection path is therefore "
                "the same period as the capital run but can carry a different CAR."
            ),
            direction="either",
            reconciliation_rule=(
                "UNRESOLVED. Until WS-B reconciles the fact scopes, the projection path CAR is "
                "ADVISORY_ONLY and must never be filed or shown as 'the' CAR. Do NOT close this "
                "by asserting equality - close it by aligning the snapshot fact scope."
            ),
            resolution_status=UNRESOLVED_AUDIT_FINDING,
            equality_assertion_forbidden=False,
            evidence=("backend/tests/api/test_forecasting.py - divergence asserted in a comment",),
        ),
        notes=(
            "Value lives at RegulatoryRun(forecast).metrics['path'][n]['car_pct'], not as a "
            "top-level metrics key."
        ),
        audit_findings=_AUDIT_FORECAST_CAR,
    )
)

# --- CAPITAL: SDI regime (Act 930 s.29) — a different law, not a duplicate

_SDI_CAPITAL_ENGINE = "app.services.sdi_capital:compute_sdi_capital_summary"

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id="car_pct",
            metric_family=MetricFamily.CAPITAL,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="act930_s29_nof_rwa",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("reference:capital_structure", "position:canonical_asset_snapshot"),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine=_SDI_CAPITAL_ENGINE,
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            parameter_set=(
                "car_min",
                "risk_weight_sovereign",
                "risk_weight_cash",
                "risk_weight_interbank",
                "risk_weight_mortgage",
                "risk_weight_other_loans",
                "risk_weight_other_assets",
            ),
            authoritative_run_type=None,
            reporting_mappings=(),
            expected_tolerance=None,
            approved_alternate_methodologies=("crd_basel_capital_run",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE
            + _FORBID_CLIENT
            + ("app.domain.capital.engine:compute_capital_ratios",),
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="Banks and SDI Act 2016 (Act 930) s.29",
            is_primary=True,
            notes=(
                "Net Own Funds / simplified RWA x 100. This is NOT Basel CAR: different legal "
                "input set, denominator and threshold. Never consolidate with the CRD engine. "
                "SDI filing family is deferred pending the BoG return pack, so this is "
                "supervisory monitoring, not a filed line, today."
            ),
            audit_findings=(
                "The SDI risk weights are seeded with confirmation_status='pending' "
                "(regulatory_parameters.SEED_PARAMETERS, 'SDI simplified risk weights "
                "(value pending BoG)'); a missing weight defaults to 100 and is reported "
                "in pending_parameters rather than silently assumed.",
            ),
        ),
        MetricAuthority(
            metric_id="total_rwa_ghs",
            metric_family=MetricFamily.CAPITAL,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="act930_s29_nof_rwa",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("position:canonical_asset_snapshot",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine=_SDI_CAPITAL_ENGINE,
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            parameter_set=(
                "risk_weight_sovereign",
                "risk_weight_cash",
                "risk_weight_interbank",
                "risk_weight_mortgage",
                "risk_weight_other_loans",
                "risk_weight_other_assets",
            ),
            reporting_mappings=(),
            approved_alternate_methodologies=("crd_basel_capital_run",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="Banks and SDI Act 2016 (Act 930) s.29",
            notes="Simplified bucket weights, no market or operational RWA component.",
            # The two prescribed-percentage-of-credit-RWA lines are the s.29
            # branch of compute_rwa (app/domain/capital/engine.py:437, :467):
            # ``params.rwa_pct_of_credit_rwa`` is populated only on this path
            # and is None on the Basel path, so these lines can only appear on
            # an SDI run. They belong here and NOT to crd_basel_capital_run -
            # binding them to the bank regime would be the exact inheritance
            # the regime-boundary rules exist to stop. Note the platform's own
            # dossier records NO published Ghanaian market-risk or
            # operational-risk charge for an SDI; the percentages therefore
            # come from the control plane per tenant, not from an instrument
            # this repository can cite, which is why this entry's
            # calculation_version already carries the sentinel.
            line_item_codes=(
                "market_rwa:market_rwa_pct_of_credit_rwa",
                "operational_rwa:operational_rwa_pct_of_credit_rwa",
            ),
        ),
        MetricAuthority(
            metric_id="net_own_funds_ghs",
            metric_family=MetricFamily.CAPITAL,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="act930_s29_nof_rwa",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("reference:capital_structure",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine=_SDI_CAPITAL_ENGINE,
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            reporting_mappings=("LE-MONTHLY",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="Banks and SDI Act 2016 (Act 930) s.29",
            notes="Also used as the Tier 1 proxy denominator in the Large Exposures templates.",
        ),
        MetricAuthority(
            metric_id="paid_up_capital_ghs",
            metric_family=MetricFamily.CAPITAL,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="act930_s29_paid_up_minimum",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("reference:capital_structure",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine="app.services.sdi_capital_checks:check_paid_up_capital",
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            parameter_set=("paid_up_min",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="Act 930 s.29 / BoG SDI Subsector Terms of Reference",
            notes=(
                "The 'other_rfi' paid_up_min seed row is confirmation_status='pending' "
                "(SDI Subsector ToR default)."
            ),
        ),
        MetricAuthority(
            metric_id="statutory_reserve_fund_ghs",
            metric_family=MetricFamily.CAPITAL,
            # Was InstitutionClass.ALL until the D-9 completeness gate measured
            # it (2026-08-22). ALL is handed to BOTH classes by
            # for_institution_class(), so a universal bank reached an Act 930
            # s.29 authority - the regime inheritance the audit warned about.
            # The claim was never true of the code either: the only callers of
            # check_statutory_reserve_fund are the s.29 diagnostics feature and
            # a branch of regulatory_capital.compute_live gated on
            # ``active.institution_class == "sdi"``. Whether Act 930 s.34 also
            # binds banks is a question about the statute; this entry describes
            # what the platform computes, and it computes it for SDIs only.
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="act930_s34_statutory_reserve",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("reference:capital_structure",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine="app.services.sdi_capital_checks:check_statutory_reserve_fund",
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            parameter_set=("statutory_reserve_fund_pct",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="Act 930 s.34; NBFI Business Rules 2000 r.7",
            notes="50% of net profit until the fund equals paid-up capital.",
        ),
    ]
)

# --- LIQUIDITY: universal bank Basel, RegulatoryRun(liquidity) ------------

REGISTRY.register_all(
    [
        _liquidity_run(
            "lcr_pct",
            engine="app.domain.liquidity.engine:compute_lcr",
            inputs=(
                "fact:hqla",
                "fact:outflow",
                "fact:inflow",
                "fact:obs_exposure",
                # The stock of HQLA is post-haircut and post-Level-2-cap since
                # 2026-08-21 (enterprise audit P0-8). Every rate is resolved from
                # the control plane; compute_lcr names none of them and refuses to
                # weight an asset whose rate is unresolved.
                "param:hqla_l1_haircut_pct",
                "param:hqla_l2a_haircut_pct",
                "param:hqla_l2b_haircut_pct",
                "param:hqla_level2_cap_pct",
                "param:hqla_level2b_cap_pct",
                "param:lcr_inflow_cap_pct",
            ),
            alternates=("lmtd_table11_capped", "bank_forecast_projection_path"),
            notes=(
                "HQLA / (weighted outflows - min(weighted inflows, outflows x cap)) x 100, "
                "where HQLA is Level 1 + Level 2A + Level 2B after their governed haircuts "
                "and after the Level-2 and Level-2B caps (BCBS 238 Annex 1). This methodology "
                "DOES cap inflows, at the governed lcr_inflow_cap_pct, once in aggregate. It "
                "differs from lmtd_table11_capped in the cap's SOURCE (governed parameter vs "
                "a hard-coded 0.75) and GRANULARITY (aggregate vs per currency column), not "
                "in whether a cap exists."
            ),
        ),
        _liquidity_run(
            "nsfr_pct",
            engine="app.domain.liquidity.engine:compute_nsfr",
            inputs=("fact:asf", "fact:rsf"),
            alternates=("bank_forecast_projection_path",),
            notes=(
                "Basel default weights. The audit records no BoG NSFR directive, so the "
                "weighting authority is Basel, not a BoG instrument."
            ),
        ),
        _liquidity_run(
            "hqla_total_ghs",
            engine="app.domain.liquidity.engine:compute_lcr",
            inputs=("fact:hqla",),
            # The two cap adjustments _hqla_stock writes as their own lines so
            # the deduction is visible rather than netted into the stock.
            lines=("hqla:hqla_level2_cap_adjustment", "hqla:hqla_level2b_cap_adjustment"),
        ),
        _liquidity_run(
            "net_outflows_30d_ghs",
            engine="app.domain.liquidity.engine:compute_lcr",
            inputs=("fact:outflow", "fact:inflow"),
            notes="Net cash outflow (NCO) - the LCR denominator.",
        ),
        _liquidity_run(
            "asf_total_ghs",
            engine="app.domain.liquidity.engine:compute_nsfr",
            inputs=("fact:asf",),
        ),
        _liquidity_run(
            "rsf_total_ghs",
            engine="app.domain.liquidity.engine:compute_nsfr",
            inputs=("fact:rsf",),
            lines=("rsf:off_balance_commitments",),
        ),
        _liquidity_run(
            "fx_funding_gap_ghs",
            engine="app.domain.liquidity.engine:compute_currency_gaps",
            inputs=("fact:currency_position",),
            reporting=("LMT",),
            notes="Per-currency funding gap (bank-facts-v3).",
        ),
        _liquidity_run(
            "stressed_fx_funding_gap_ghs",
            engine="app.domain.liquidity.engine:compute_stressed_ladder",
            inputs=("fact:currency_position", "param:usd_funding_stress"),
            reporting=("LMT",),
            notes="Behavioural stress assumptions govern this figure; see the audit's "
            "'behavioral assumption governance' medium risk.",
        ),
    ]
)

# --- LIQUIDITY: THE encoded divergence — LMT Table 11 capped LCR ----------

REGISTRY.register(
    MetricAuthority(
        metric_id="lcr_pct",
        metric_family=MetricFamily.LIQUIDITY,
        institution_class=InstitutionClass.ALL,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.LMTD,
        methodology_id="lmtd_table11_capped",
        return_family="liquidity",
        effective_from=_EPOCH,
        canonical_inputs=("position:canonical_row", "reference:hqla_classification"),
        policy_resolver=_PARAMS_CONTROL,
        calculation_engine="app.services.regulatory_reporting.le_generation:_table11_section",
        calculation_version="lmt/bog-lmt-liquidity-v1",
        parameter_set=("_LCR_INFLOW_CAP",),
        authoritative_run_type=None,
        reporting_mappings=("LMT!lcr_by_currency.lcr_pct",),
        expected_tolerance=None,
        forbidden_alternative_sources=_FORBID_CASE_PLANE,
        advisory_designation=AdvisoryDesignation.FILED,
        authority_reference="BoG Liquidity Monitoring Tools Directive, paragraphs 39-43 (Table 11)",
        instrument_in_force=False,
        is_primary=False,
        divergence=MethodologyDivergence(
            versus_methodology_id="basel_bog_liquidity_run",
            authority_reference="LMTD paragraphs 39-43 (Annex, Table 11)",
            reason=(
                "BOTH methodologies cap inflows; the divergence is in HOW. Table 11 caps "
                "total cash inflow at a hard-coded 75% of total cash outflow "
                "(le_generation._LCR_INFLOW_CAP = Decimal('0.75')) SEPARATELY FOR EACH "
                "CURRENCY COLUMN at LMT generation time. The LCR-NSFR return applies one "
                "AGGREGATE cap across the whole book, at the governed, effective-dated "
                "lcr_inflow_cap_pct threshold (required by "
                "regulatory_liquidity._REQUIRED_THRESHOLDS, applied unconditionally in "
                "domain/liquidity/engine.compute_lcr). An earlier version of this entry "
                "said the LCR-NSFR return applies no cap. That was FALSE - do not act on it."
            ),
            direction="lower",
            reconciliation_rule=(
                "NOT reconciled by equality. Both are correct under their own authority. The "
                "Table 11 value is <= the LCR-NSFR value whenever the cap binds, and equal "
                "when it does not. Any test asserting equality is wrong."
            ),
            resolution_status=ACCEPTED_BY_AUTHORITY,
            evidence=(
                "backend/app/services/regulatory_reporting/le_generation.py - "
                "_LCR_INFLOW_CAP at L1323, applied per currency column at L1469 "
                "(the audits cite L1318/L1464; those line numbers are stale)",
                "backend/app/domain/liquidity/engine.py - the aggregate inflow cap at "
                "L264, from the governed params.inflow_cap_pct",
                "backend/tests/services/test_le_and_lmt.py",
                "forensic_calculation_audit_2026-08-21.md - CRITICAL AUDIT FINDINGS 1: "
                "'BSD3 LCR != LMT Table 11 LCR (Documented Divergence)'",
            ),
        ),
        notes=(
            "The audit calls the counterpart 'BSD3 LCR'. In this repository that return is "
            "coded LCR-NSFR (migration 202608150013); official BSD3A/BSD3B are the Large "
            "Exposures returns. Known documented deviation on this table: Level 2A / Level 2B "
            "report zero because the canonical model carries no Level-2 taxonomy."
        ),
        audit_findings=(
            "forensic_calculation_audit_2026-08-21.md - 'Risk: BSD3 does NOT apply the cap; "
            "LMT Table 11 does. Two different LCR% values possible.'",
        ),
    )
)

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=metric_id,
            metric_family=MetricFamily.LIQUIDITY,
            institution_class=InstitutionClass.BANK,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ADVISORY_INTERNAL,
            methodology_id="bank_forecast_projection_path",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("fact:bank_facts_v2", "param:forecast_assumptions"),
            policy_resolver=_PARAMS_TENANT,
            calculation_engine="app.domain.forecasting.engine:project",
            calculation_version=_FORECAST_VERSION,
            parameter_set=("ParamLcrRunoffRate", "ParamNsfrWeight"),
            authoritative_run_type="forecast",
            expected_tolerance=None,
            forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
            advisory_designation=AdvisoryDesignation.ADVISORY_ONLY,
            authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            is_primary=False,
            divergence=MethodologyDivergence(
                versus_methodology_id="basel_bog_liquidity_run",
                authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
                reason=(
                    "Year 0 of the forecast path re-derives the ratio from the projected fact "
                    "set rather than reading the liquidity run. The audit found year-0 LCR and "
                    "NSFR equal under the tested baseline, but the equality is not enforced."
                ),
                direction="either",
                reconciliation_rule=(
                    "UNRESOLVED but LOW severity (audit: 'Safe shared-engine duplication'). "
                    "WS-B should make year 0 read the liquidity run, or add an equivalence "
                    "test at Decimal('0') tolerance. Until then: ADVISORY_ONLY."
                ),
                resolution_status=UNRESOLVED_AUDIT_FINDING,
                equality_assertion_forbidden=False,
                evidence=(
                    "FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md section 5 - "
                    "'Forecast year-0 LCR/NSFR ... Yes for tested baseline'",
                ),
            ),
            notes=(
                "Value lives at RegulatoryRun(forecast).metrics['path'][n] - not a top-level "
                "metrics key."
            ),
        )
        for metric_id in ("lcr_pct", "nsfr_pct")
    ]
)

# --- LIQUIDITY: LMTD Table 1 ratios (class-neutral formula, class floors) --

_LMTD_TABLE1_RATIOS: tuple[tuple[str, str], ...] = (
    ("narrow_to_volatile", "Narrow liquid assets / volatile liabilities"),
    ("broad_to_volatile", "Broad liquid assets / volatile liabilities"),
    ("narrow_to_short_term", "Narrow liquid assets / short-term liabilities"),
    ("broad_to_short_term", "Broad liquid assets / short-term liabilities"),
    ("narrow_to_total_deposits", "Narrow liquid assets / total deposits"),
    ("broad_to_total_deposits", "Broad liquid assets / total deposits"),
    ("narrow_to_total_assets", "Narrow liquid assets / total assets"),
    ("broad_to_total_assets", "Broad liquid assets / total assets"),
)

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=code,
            metric_family=MetricFamily.LIQUIDITY,
            institution_class=InstitutionClass.ALL,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.LMTD,
            methodology_id="lmtd_table1_ratio",
            return_family="liquidity",
            effective_from=_EPOCH,
            canonical_inputs=("position:canonical_row",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine="app.services.regulatory_reporting.le_generation:_table1_inputs",
            calculation_version="lmt/bog-lmt-liquidity-v1",
            parameter_set=(code, "ParamLiquidityThreshold"),
            authoritative_run_type=None,
            reporting_mappings=("LMT",),
            expected_tolerance=Decimal("0"),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference="BoG Liquidity Monitoring Tools Directive 2026, paragraph 9",
            instrument_in_force=False,
            notes=(
                f"{label}. One formula, two floor sets: the bank and SDI floors differ "
                "(regulatory_parameters._LMTD_FLOORS). _table1_thresholds fails loud with "
                "'sdi_liquidity_floor_unseeded' rather than falling back to the bank floors."
            ),
        )
        for code, label in _LMTD_TABLE1_RATIOS
    ]
)

# --- LIQUIDITY: SDI statutory reserves (NBFI Business Rules) ---------------

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=code,
            metric_family=MetricFamily.LIQUIDITY,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="nbfi_r11_liquidity_reserve",
            return_family=None,
            effective_from=_EPOCH,
            canonical_inputs=("position:canonical_row",),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine="app.services.sdi_views:get_sdi_liquidity_position",
            calculation_version=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            parameter_set=(code,),
            reporting_mappings=(),
            expected_tolerance=None,
            forbidden_alternative_sources=_FORBID_CASE_PLANE
            + ("app.domain.liquidity.engine:compute_lcr",),
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference="NBFI Business Rules 2000, rule 11",
            notes="A statutory reserve requirement, not an LCR. Never map it onto HQLA.",
        )
        for code in ("primary_liquidity_reserve_pct", "secondary_liquidity_reserve_pct")
    ]
)

# --- IRRBB ----------------------------------------------------------------

REGISTRY.register_all(
    [
        _irr(
            "eve_base_ghs",
            engine="app.domain.irr.engine:compute_eve",
            notes="Economic Value of Equity, baseline curve.",
            lines=("irr_eve:base",),
        ),
        _irr(
            "worst_eve_change_pct_tier1",
            engine="app.domain.irr.engine:run_irr_scenarios",
            notes="Worst delta-EVE across the prescribed shocks, as % of Tier 1.",
        ),
        _irr("ear_up_200_ghs", engine="app.domain.irr.engine:compute_ear"),
        _irr("ear_down_200_ghs", engine="app.domain.irr.engine:compute_ear"),
        # The +/-450 bp pair is the SAME engine, run, methodology and citation as
        # the +/-200 bp pair above; only the shock magnitude differs, and it is the
        # BETTER-governed of the two — 200 is the code constant
        # ``irr.engine.EAR_UP_BP``, 450 arrives from the tenant's governed
        # ``ParamStressShock`` rows (``parallel_up_450`` / ``parallel_down_450``),
        # which is why these rows are conditional on the parameter set carrying
        # them. Registering them asserts no legal basis that ``ear_up_200_ghs`` did
        # not already assert; it records that the platform files four EaR figures,
        # not two. What the magnitude itself rests on is in the note.
        _irr(
            "ear_up_450_ghs",
            engine="app.domain.irr.engine:compute_ear",
            notes=(
                "Earnings at Risk under the +450 bp parallel shock. The magnitude is "
                "Ghanaian, not a Basel default: BoG Guideline on the Management and "
                "Measurement of Interest Rate Risk in the Banking Book, 2026, Appendix II "
                "P1 and Table 5 (printed page 39) set the Ghana cedi parallel shift at 450 "
                "basis points and make it mandatory for EVE and NII. The cedi is absent "
                "from BCBS d368 Annex 2, so no Basel table supplies it. That guideline is "
                "an EXPOSURE DRAFT (February 2026, effective 1 January 2027 at P9), which "
                "is why the methodology's citation still carries the sentinel. Persisted "
                "only when the active parameter set carries the parallel_up_450 shock; "
                "app/services/scenario_catalog.py labels the scenario 'informational', "
                "which is correct while the guideline is not in force and becomes wrong "
                "on the day it is."
            ),
        ),
        _irr(
            "ear_down_450_ghs",
            engine="app.domain.irr.engine:compute_ear",
            notes=(
                "Earnings at Risk under the -450 bp parallel shock; same instrument, "
                "table and exposure-draft status as ear_up_450_ghs."
            ),
        ),
        _irr(
            "nii_base_ghs",
            engine="app.domain.irr.engine:compute_nii",
            notes="Net interest income, baseline.",
        ),
        _irr("duration_gap", engine="app.domain.irr.engine:compute_duration"),
        # The two terms of the gap identity that duration_gap already stands on:
        # ``duration_gap = asset_modified - (PV_liabilities / PV_assets) *
        # liability_modified`` (app/domain/irr/engine.py:338). One call to
        # compute_duration returns all three. An authority over the difference
        # that does not reach its own operands is not an authority over anything,
        # so these carry the SAME citation rather than a new one.
        _irr(
            "asset_duration",
            engine="app.domain.irr.engine:compute_duration",
            notes=(
                "Modified duration of assets - the first term of the duration gap "
                "identity, from the same compute_duration call as duration_gap."
            ),
        ),
        _irr(
            "liability_duration",
            engine="app.domain.irr.engine:compute_duration",
            notes=(
                "Modified duration of liabilities - the second term of the duration gap "
                "identity, weighted by PV_liabilities / PV_assets in the gap itself."
            ),
        ),
        _irr("cumulative_12m_gap_ghs", engine="app.domain.irr.engine:compute_gap"),
    ]
)

# --- FX -------------------------------------------------------------------

REGISTRY.register_all(
    [
        _fx(
            "nop_ghs",
            engine="app.domain.fx.engine:compute_nop",
            notes="Net open position, aggregated across currencies.",
        ),
        _fx("nop_pct_tier1", engine="app.domain.fx.engine:compute_nop"),
        _fx("single_ccy_max_pct", engine="app.domain.fx.engine:compute_nop"),
        # The two value-at-risk figures are SUPERVISORY_MONITORING, not FILED, and
        # their citation is a stated negative rather than the pending-verification
        # sentinel. The distinction matters: the sentinel means "the instrument
        # exists and this repository has not located it", which is the honest
        # state of the three net-open-position metrics above. For value at risk
        # the search has been done and returned nothing —
        # backend/docs/bog_parameter_sources.md section 7 records "Value-at-risk
        # or internal models | Banks | not permitted and not required; absent from
        # the directive | VERIFIED (negative)", against a full-text search of the
        # Capital Requirements Directive, which mandates the Standardised Method
        # at paragraph 310. So these are management measures. They are still
        # surfaced on the FX packages named in reporting_mappings — the same
        # arrangement as the enterprise-stress metrics, which are supervisory
        # figures tabulated in the STRESS-PACK — but nothing here asserts that a
        # regulator requires them.
        #
        # Also NOT bound here: fx_var:diversification_benefit. That line is no
        # longer written at all (app/domain/fx/engine.py::compute_var); the
        # residual of two value-at-risk figures cannot have a firmer basis than
        # the figures themselves, and it was reaching a sealed run on both planes
        # — as an fx_var line item and as the diversification_benefit_ghs metric
        # result. Both were removed rather than registered. The figure survives on
        # run.metrics and FxMetricsRead for the VaR waterfall.
        _fx(
            "var_99_1d_ghs",
            engine="app.domain.fx.engine:compute_var",
            notes=(
                "99% 1-day Value at Risk. Management measure: no BoG instrument "
                "establishes a value-at-risk requirement for banks."
            ),
            lines=("fx_var:portfolio_var",),
            management_measure=_NO_VAR_AUTHORITY,
        ),
        _fx(
            "stressed_var_ghs",
            engine="app.domain.fx.engine:compute_stressed_var",
            notes=(
                "Cedi-crisis stressed VaR with a supervisory correlation uplift. "
                "Same standing as var_99_1d_ghs: a stress calibration applied to a "
                "measure the directive does not recognise cannot itself be filed."
            ),
            lines=("fx_var:stressed_var",),
            management_measure=_NO_VAR_AUTHORITY,
        ),
    ]
)

# --- FTP (advisory only — no external regulatory authority) ---------------

REGISTRY.register_all(
    [
        _ftp("portfolio_nim_pct", engine="app.domain.ftp.engine:product_profitability"),
        _ftp("weighted_asset_yield_pct", engine="app.domain.ftp.engine:product_profitability"),
        _ftp("weighted_funding_credit_pct", engine="app.domain.ftp.engine:build_curve"),
        _ftp("nmd_core_pct", engine="app.domain.ftp.engine:nmd_split"),
        _ftp(
            "total_branch_contribution_ghs",
            engine="app.domain.ftp.engine:branch_profitability",
            forbidden=("dashboard/components/ftp/businessLines.ts",),
            notes=(
                "The audit found a presentational shadow calculation on the dashboard: "
                "sum(contribution) / sum(balance) grouped by business line, a grouping the "
                "backend does not produce. It is non-filing but must not be presented as an "
                "engine figure."
            ),
        ),
    ]
)

# --- FORECAST summary metrics --------------------------------------------

REGISTRY.register_all(
    [
        _forecast("year5_car_pct"),
        _forecast("year5_lcr_pct"),
        _forecast("year5_nsfr_pct"),
        _forecast("min_car_pct"),
        _forecast("min_lcr_pct"),
        _forecast("min_nsfr_pct"),
        _forecast("avg_roe_pct"),
        _forecast("cumulative_net_income"),
    ]
)

# --- STRESS ---------------------------------------------------------------


def _stress(metric_id: str, *, notes: str = "") -> MetricAuthority:
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.STRESS,
        institution_class=InstitutionClass.ALL,
        jurisdiction=_GH,
        regulator=_BOG,
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="enterprise_stress_orchestrator",
        return_family="stress",
        effective_from=_EPOCH,
        canonical_inputs=(
            "fact:bank_facts_v2",
            "param:macro_scenario",
            "param:management_action_plan",
        ),
        policy_resolver=_PARAMS_TENANT,
        calculation_engine="app.domain.stress.orchestrator:run_enterprise_stress",
        calculation_version=_STRESS_VERSION,
        parameter_set=("ParamStressShock", "ParamEclAssumption", "car_min", "paid_up_min"),
        authoritative_run_type="enterprise_stress",
        reporting_mappings=("STRESS-PACK", "ICAAP-STRESS-APPENDIX2"),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
        authority_reference=(
            "BoG ICAAP / stress-testing directive "
            f"({EXTERNAL_REGULATORY_VERIFICATION_REQUIRED} for a prescribed macro scenario set)"
        ),
        notes=notes,
        audit_findings=(
            "Audit: enterprise_stress and domain/stress/translation.py carry CODE DEFAULTS for "
            "baseline assumptions and stress elasticities - a High policy-governance risk. "
            "Also: ENGINE_VERSION is declared twice (services/enterprise_stress.py and "
            "domain/stress/orchestrator.py); only the orchestrator's value reaches "
            "metrics['outcome']['engine_version'].",
        ),
    )


REGISTRY.register_all(
    [
        _stress(
            "stressed_car_end_pct", notes="metrics['outcome']['capital']['stressed_car_end_pct']."
        ),
        _stress("car_erosion_pp", notes="metrics['outcome']['capital']['car_erosion_pp']."),
        _stress(
            "stressed_lcr_pct",
            notes="metrics['outcome']['liquidity']['stressed_lcr_pct']. Absent for SDI "
            "tenants, which carry the marker {assessed: False, regime: 'sdi_lmtd'}.",
        ),
        _stress("lcr_erosion_pp", notes="metrics['outcome']['liquidity']['lcr_erosion_pp']."),
    ]
)

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=metric_id,
            metric_family=MetricFamily.STRESS,
            institution_class=InstitutionClass.BANK,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ADVISORY_INTERNAL,
            methodology_id="reverse_stress_frontier",
            return_family="stress",
            effective_from=_EPOCH,
            canonical_inputs=("fact:bank_facts_v2", "param:baseline_thresholds"),
            policy_resolver=_PARAMS_TENANT,
            calculation_engine=engine,
            calculation_version=_REVERSE_VERSION,
            parameter_set=("ParamStressShock", "ParamCapitalThreshold"),
            authoritative_run_type="reverse_stress",
            reporting_mappings=("STRESS-PACK", "ICAAP-STRESS-APPENDIX2"),
            expected_tolerance=Decimal("0"),
            forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            notes=(
                f"Persisted at metrics['{axis}']['breach_multiplier']. The frontier search "
                "constants (k_max = 5, precision = 0.05) are code defaults, not governed "
                "parameters. This run writes NO RegulatoryMetricResult rows."
            ),
        )
        for metric_id, axis, engine in (
            (
                "capital_breach_multiplier",
                "capital_axis",
                "app.services.regulatory_capital:capital_breach_multiplier",
            ),
            (
                "liquidity_breach_multiplier",
                "liquidity_axis",
                "app.services.regulatory_liquidity:liquidity_breach_multiplier",
            ),
        )
    ]
)

# --- CREDIT: loan classification (one engine, two legal grids) ------------

_CREDIT_ENGINE = "app.domain.capital.loan_classification:classify_book"

#: Every figure the credit run persists as a RegulatoryMetricResult, plus the
#: legacy ``npl_ratio`` fraction id the SDI read-side already serves. One list
#: for both regimes: registration says who OWNS a figure when it exists — a
#: metric a class does not emit (OLEM for an SDI) simply never appears.
_CREDIT_METRIC_IDS = (
    "npl_ratio",
    "npl_ratio_pct",
    "gross_loans_ghs",
    "npl_exposure_ghs",
    "total_provision_required_ghs",
    "provision_held_ghs",
    "provision_coverage_pct",
    "par_30_pct",
    "par_60_pct",
    "par_90_pct",
    "unclassified_exposure_ghs",
)

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=metric_id,
            metric_family=MetricFamily.CREDIT,
            institution_class=InstitutionClass.BANK,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.CRD_BASEL,
            methodology_id="bog_five_grade_classification",
            return_family="bsd",  # BSD5A/BSD8 remain the bank's classification returns
            effective_from=_EPOCH,
            canonical_inputs=("position:canonical_loan", "fact:days_past_due", "fact:ifrs9_stage"),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine=_CREDIT_ENGINE,
            calculation_version="regulatory-credit-v1.0.0",
            parameter_set=(
                "prov_standard",
                "prov_olem",
                "prov_substandard",
                "prov_doubtful",
                "prov_loss",
                "npl_dpd_threshold",
                "dpd_olem_min",
                "dpd_substandard_min",
                "dpd_doubtful_min",
                "dpd_loss_min",
            ),
            authoritative_run_type="credit",
            reporting_mappings=("BSD5A", "BSD8", "NPL-MONTHLY"),
            expected_tolerance=Decimal("0"),
            approved_alternate_methodologies=("nbfi_four_grade_classification",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference="BoG loan classification (5-grade: standard/olem/substandard/"
            "doubtful/loss)",
            notes="Same engine as the SDI grid; the GRADE SET and provisioning rates differ. "
            "Sealed by RegulatoryRun(module='credit') from credit PR-2; the NPL prudential "
            "ceiling is Notice BG/GOV/SEC/2025/23 (npl_limit_pct).",
        )
        for metric_id in _CREDIT_METRIC_IDS
    ]
)

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=metric_id,
            metric_family=MetricFamily.CREDIT,
            institution_class=InstitutionClass.SDI,
            jurisdiction=_GH,
            regulator=_BOG,
            regime=Regime.ACT930_S29,
            methodology_id="nbfi_four_grade_classification",
            return_family="credit",
            effective_from=_EPOCH,
            canonical_inputs=("position:canonical_loan", "fact:days_past_due", "fact:ifrs9_stage"),
            policy_resolver=_PARAMS_CONTROL,
            calculation_engine=_CREDIT_ENGINE,
            calculation_version="regulatory-credit-v1.0.0",
            parameter_set=(
                "prov_standard",
                "prov_substandard",
                "prov_doubtful",
                "prov_loss",
                "npl_dpd_threshold",
                "dpd_substandard_min",
                "dpd_doubtful_min",
                "dpd_loss_min",
            ),
            authoritative_run_type="credit",
            reporting_mappings=("NPL-MONTHLY",),
            expected_tolerance=Decimal("0"),
            approved_alternate_methodologies=("bog_five_grade_classification",),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            # FILED since credit PR-6: the NPL-MONTHLY return (Notice 2025/23,
            # in force) files the 4-grade figures for an SDI.
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference="NBFI Business Rules 2000, rules 17-19 (4-grade: standard/"
            "substandard/doubtful/loss)",
            notes="No OLEM grade; provisioning rates 0/20/50/100 rather than 1/10/25/50/100. "
            "Sealed by RegulatoryRun(module='credit'); flips to FILED when the NPL-MONTHLY "
            "return registers (credit PR-6).",
        )
        for metric_id in _CREDIT_METRIC_IDS
    ]
)

# --- IFRS 9 ECL (class-neutral, one engine, one authority) ----------------

REGISTRY.register_all(
    [
        MetricAuthority(
            metric_id=metric_id,
            metric_family=MetricFamily.CREDIT,
            institution_class=InstitutionClass.ALL,
            jurisdiction="*",
            regulator="IASB / IFRS 9",
            regime=Regime.IFRS9,
            methodology_id="ifrs9_pd_lgd_ead",
            return_family="capital",
            effective_from=_EPOCH,
            canonical_inputs=("fact:ecl_exposure", "register:ecl-assumptions"),
            policy_resolver=_PARAMS_TENANT,
            calculation_engine="app.domain.capital.ecl:compute_ecl",
            calculation_version=_CAPITAL_VERSION,
            parameter_set=("ParamEclAssumption",),
            authoritative_run_type="capital",
            reporting_mappings=("CAR-RWA",),
            expected_tolerance=Decimal("0"),
            forbidden_alternative_sources=_FORBID_CASE_PLANE,
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference="IFRS 9 paragraph 5.5.17 (unbiased probability-weighted amount)",
            notes=(
                "Active ONLY when ecl_exposure facts AND the ecl-assumptions register both "
                "exist; otherwise the ingested-provisions path is byte-identical. An exposure "
                "whose (segment, stage) has no assumption is reported UNCOVERED, never priced "
                "at zero - the caller decides whether that blocks the run."
            ),
        )
        for metric_id in ("ecl_total_ghs", "ecl_general_ghs", "ecl_specific_ghs")
    ]
)


# --- CREDIT: implied bank rating and PD (advisory only) -------------------

#: The scorecard's own parameter register version. ``app.services.implied_rating``
#: is already one of the engine modules the registry test suite imports, so this
#: string cannot drift from the constant it copies.
_RATING_VERSION = "rating-scorecard/1.0"

#: An assertion, not a pending question. AequorOS' implied rating is a
#: proprietary scorecard: it maps governed financial ratios onto an internal
#: master scale, conditions the through-the-cycle PD to a point-in-time PD by
#: Vasicek systematic conditioning, and caps the issuer at the sovereign ceiling
#: read from the jurisdiction registry. The Bank of Ghana prescribes no internal
#: rating or PD methodology for a bank rating itself, and none is claimed here.
#: The sentinel would be the wrong answer: it means "an instrument exists and
#: this repository has not located it", and there is no such instrument to find.
_RATING_NO_AUTHORITY = (
    "Internal AequorOS scorecard methodology (AEQ-GHS-BANK-PD) - no external regulatory "
    "authority. No BoG instrument prescribes an internal rating or probability-of-default "
    "methodology for a bank's own credit standing."
)

_RATING_NOTES = (
    "Published to the live plane only (app/services/implied_rating.py::compute_live) and "
    "sealed into ImpliedRatingRun, never into a RegulatoryRun. The module feeds no return: "
    "nothing under app/services/regulatory_reporting/ reads it. ADVISORY_ONLY is therefore "
    "the operative constraint as well as the honest label - these figures must not reach a "
    "filing, and today none of them can."
)


def _rating(metric_id: str, *, engine: str, notes: str = "") -> MetricAuthority:
    return MetricAuthority(
        metric_id=metric_id,
        metric_family=MetricFamily.CREDIT,
        institution_class=InstitutionClass.BANK,
        jurisdiction=_GH,
        regulator="internal",
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="aequoros_implied_rating_scorecard",
        return_family=None,
        effective_from=_EPOCH,
        # The four module dependencies are named WITHOUT a metric suffix on
        # purpose: _live_dependency_metrics consumes each module's whole live
        # payload, not a named figure, and it is the LIVE tier it reads rather
        # than a sealed RegulatoryRun (implied_rating.py:1103-1119). Naming a
        # metric here would claim a precision the code does not have.
        canonical_inputs=(
            "fact:current_financial_facts",
            "live:capital",
            "live:liquidity",
            "live:irr",
            "live:fx",
            "reference:sovereign_counterparty_rating",
            "param:desk_methodology.AEQ-GHS-BANK-PD",
        ),
        policy_resolver="app.services.implied_rating:ensure_default_methodology",
        calculation_engine=engine,
        calculation_version=_RATING_VERSION,
        parameter_set=("DeskMethodology.AEQ-GHS-BANK-PD",),
        authoritative_run_type=None,
        reporting_mappings=(),
        expected_tolerance=Decimal("0"),
        forbidden_alternative_sources=_FORBID_CASE_PLANE + _FORBID_CLIENT,
        advisory_designation=AdvisoryDesignation.ADVISORY_ONLY,
        authority_reference=_RATING_NO_AUTHORITY,
        notes=notes or _RATING_NOTES,
    )


_RATING_ENGINE = "app.domain.rating.engine:compute_rating"
_DDEP_ENGINE = "app.domain.rating.engine:ddep_stress"

REGISTRY.register_all(
    [
        *(
            _rating(metric_id, engine=_RATING_ENGINE)
            for metric_id in (
                "pit_rating_grade",
                "ttc_rating_grade",
                "standalone_grade",
                "pit_pd_lower_pct",
                "pit_pd_point_pct",
                "pit_pd_upper_pct",
                "pit_pd_central_pct",
                "pit_systematic_factor",
                "ttc_pd_lower_pct",
                "ttc_pd_point_pct",
                "ttc_pd_upper_pct",
                "ttc_pd_central_pct",
            )
        ),
        _rating(
            "sovereign_ceiling",
            engine=_RATING_ENGINE,
            notes=(
                "The issuer grade is capped at the sovereign's grade, read from the "
                "jurisdiction registry's sovereign_rating_issuer and the tenant's own "
                "ingested counterparty ratings. The CAP is the platform's convention, not "
                "a BoG rule; the sovereign grade itself is a third-party agency opinion "
                "the tenant ingested. " + _RATING_NOTES
            ),
        ),
        *(
            _rating(
                metric_id,
                engine=_DDEP_ENGINE,
                notes=(
                    "Domestic Debt Exchange Programme sensitivity: a governed haircut is "
                    "applied to sovereign holdings and the resulting capital position is "
                    "re-tested. This is scenario analysis of a past Ghanaian event, not a "
                    "supervisory stress test, and no instrument prescribes the haircut. "
                    + _RATING_NOTES
                ),
            )
            for metric_id in ("ddep_eligible", "ddep_post_stress_capital_ratio_pct")
        ),
    ]
)

# --- module-level convenience --------------------------------------------


def get_authority(metric_id: str, *, regime: Regime, methodology_id: str) -> MetricAuthority:
    """The single declared authority for a metric under one regime+methodology."""
    return REGISTRY.get(metric_id, regime=regime, methodology_id=methodology_id)


def authorities_for_metric(metric_id: str) -> tuple[MetricAuthority, ...]:
    """Every registered authority sharing this metric name."""
    return REGISTRY.for_metric(metric_id)


def all_authorities() -> tuple[MetricAuthority, ...]:
    return REGISTRY.all()


def multi_authority_metrics() -> Mapping[str, tuple[MetricAuthority, ...]]:
    """Metric names produced by more than one declared method."""
    return REGISTRY.multi_authority_metrics()


def check_completeness(evidence: CodeEvidence) -> tuple[CompletenessFailure, ...]:
    """Measure the live registry against what the repository actually does."""
    return REGISTRY.check_completeness(evidence)
