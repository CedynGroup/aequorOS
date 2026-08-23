"""Institution-type resolution for services (docs/sdi.md §1.2) — FAIL CLOSED.

The institution discriminator is DATA: it lives in the global
``institution_types`` registry and resolves through the bank's typed
``institution_type`` code, exactly as country identity resolves through
``jurisdiction_code`` (``app/services/jurisdictions.py``). Services and
calculation engines that need the derived regime — the coarse
``institution_class`` ('bank'|'sdi'), the return family, the exposure limits —
call these helpers instead of hardcoding ``"bank"``.

Fail-closed discipline (P0-12, enterprise audit 2026-08-20)
----------------------------------------------------------
Until 2026-08-21 an unknown or blank ``institution_type`` resolved to the named
``universal_bank`` row. That single substitution selected the CAR floor, the
provisioning grid, the DPD boundaries, the LMTD floors and whether Basel
LCR/NSFR applies — and because ``universal_bank.default_modules`` is the FULL
module set it made ``require_module_access`` fail **open**: a typo granted an SDI
complete bank-module access rather than denying it. The module's own docstring
claimed "a derived regulatory attribute is never silently defaulted", which was
false as written.

There is now **no fallback**. An institution type that is blank, or absent from
the registry, raises :class:`InstitutionTypeUnresolved`. That is deliberate and
is the whole point: no regime can be selected for an institution whose licence
class is unknown, so no regulatory number may be produced for it — and no module
may be granted to it.

The error is an ``HTTPException`` (409 Conflict, the codebase's configured-state
conflict code — ``app/core/errors.py`` already documents 409 that way) so an API
caller receives a precise, actionable message instead of a 500, and it carries
WS-A's ``POLICY_UNRESOLVED`` outcome detail so a service that persists
fail-closed states against a run can record it like any other.

Callers that legitimately need to DEGRADE rather than abort — a list view that
shows an unresolved tenant as unresolved — use :func:`try_get_type`, which
returns ``None`` and never substitutes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import NotComputable, OutcomeDetail, OutcomeState, outcome
from app.models import Bank, InstitutionType

#: The licence class the resolver USED to substitute when a bank's own type did
#: not resolve. Retained only as the seed/registry sentinel other modules import;
#: it is no longer a fallback and must never be reintroduced as one (P0-12).
FALLBACK_TYPE_CODE = "universal_bank"


class InstitutionTypeUnresolved(HTTPException, NotComputable):
    """The bank's licence class does not resolve — no regime can be selected.

    Doubly typed on purpose: ``HTTPException`` so an API request fails with a
    409 and a precise message rather than a 500, ``NotComputable`` so a worker or
    engine boundary that already handles WS-A fail-closed outcomes handles this
    one identically (``exc.details``, ``exc.blocks_filing``, ``exc.to_dict()``).
    """

    def __init__(self, detail: OutcomeDetail) -> None:
        NotComputable.__init__(self, detail)
        HTTPException.__init__(
            self,
            status_code=status.HTTP_409_CONFLICT,
            detail=detail.message,
        )


def _unresolved(bank: Bank, *, registry_empty: bool) -> InstitutionTypeUnresolved:
    code = (bank.institution_type or "").strip()
    if not code:
        reason = (
            f"Bank {bank.id} has no institution_type. The licence class is REQUIRED and "
            "carries no default: an unset value means the creation site skipped a "
            "required decision, not that the institution is a universal bank. Set it "
            "from the institution_types registry before any regulatory figure is "
            "produced for this institution."
        )
    elif registry_empty:
        reason = (
            f"Bank {bank.id} institution_type {code!r} cannot be resolved because the "
            "institution_types registry is empty. The registry seed migration "
            "(202608190018_institution_types_registry) is a hard prerequisite."
        )
    else:
        reason = (
            f"Bank {bank.id} institution_type {code!r} is not in the institution_types "
            "registry, so its regulatory regime (capital regime, return family, "
            "exposure limits, module entitlement) cannot be determined. It is NOT "
            "treated as a universal bank. Register the licence class, or correct the "
            "institution's type."
        )
    return InstitutionTypeUnresolved(
        outcome(
            OutcomeState.POLICY_UNRESOLVED,
            metric_id="institution_type",
            reason=reason,
            items=(f"bank:{bank.id}", f"institution_type:{code or '<unset>'}"),
            context={
                "bank_id": bank.id,
                "organization_id": bank.organization_id,
                "institution_type": code or None,
            },
        )
    )


def try_get_type(db: Session, bank: Bank) -> InstitutionType | None:
    """The bank's registry row, or ``None`` — never a substitute.

    For callers that must DEGRADE gracefully (a listing that renders an
    unresolved tenant as unresolved). Anything that selects a regulatory regime
    must use :func:`get_type` and let it fail.
    """
    code = (bank.institution_type or "").strip()
    if not code:
        return None
    return db.get(InstitutionType, code)


def get_type(db: Session, bank: Bank) -> InstitutionType:
    """Resolve the bank's institution-type registry row (FAIL CLOSED).

    Raises :class:`InstitutionTypeUnresolved` when the bank's ``institution_type``
    is blank or has no registry row. There is no fallback row: substituting the
    bank regime for an unknown licence class is P0-12.
    """
    row = try_get_type(db, bank)
    if row is not None:
        return row
    registry_empty = db.query(InstitutionType).first() is None
    raise _unresolved(bank, registry_empty=registry_empty)


def institution_class(db: Session, bank: Bank) -> str:
    """The coarse regime axis ('bank' | 'sdi') — the value the threshold and
    capital registers key on. Derived from the licence class; fail-closed."""
    return get_type(db, bank).institution_class


def return_family(db: Session, bank: Bank) -> str:
    """The BoG return family the institution files ('bsd' | 'sdi')."""
    return get_type(db, bank).return_family


def capital_regime(db: Session, bank: Bank) -> str:
    """The capital regime ('crd' Basel banks | 's29' Act 930 SDIs)."""
    return get_type(db, bank).capital_regime


def large_exposure_limit_pct(db: Session, bank: Bank) -> Decimal:
    """Per-obligor large-exposure limit as a % of Net Own Funds (bank 20 / SDI 15)."""
    return get_type(db, bank).large_exposure_limit_pct


def single_obligor_limit_pct(db: Session, bank: Bank) -> Decimal:
    """Statutory single-obligor limit as a % of NOF (25 across classes)."""
    return get_type(db, bank).single_obligor_limit_pct


def liquidity_binding(db: Session, bank: Bank) -> bool:
    """Whether the LMTD Table 1 prudential ratios BIND (SDIs) or only monitor (banks)."""
    return get_type(db, bank).liquidity_binding


def default_modules(db: Session, bank: Bank) -> list[str]:
    """The module set this licence class is entitled to.

    Used by the API module gate. Fail-closed by construction: an unresolved
    licence class raises rather than returning the universal-bank superset, so a
    typo denies access instead of granting it.
    """
    return list(get_type(db, bank).default_modules or [])


# ---------------------------------------------------------------------------
# Seed catalogue
# ---------------------------------------------------------------------------
#
# The single authoritative catalogue of licence classes, shared by every path
# that has to CREATE the registry from a bare schema — the hermetic test seed
# (``tests/conftest.py``) and the e2e bootstrap
# (``scripts/e2e_bootstrap.py``), both of which build with
# ``Base.metadata.create_all`` and therefore never run the seed migration.
# It mirrors the idiom already used by the regulatory-parameter control plane
# (``regulatory_parameters.seed_rows``): one catalogue, no per-caller copies.
#
# Relationship to the migrations: the migration chain must ARRIVE at this state
# (``202608190018`` created the registry, ``202608210026`` widened the SDI module
# set). Those migrations keep their own historical snapshots on purpose — a
# migration must describe the change it made — so the invariant is pinned by a
# test (``tests/services/test_institution_types.py::
# test_seed_catalogue_matches_the_migration_chain_end_state``) rather than by an
# import. Change a licence class here and that test tells you whether a new
# migration is owed.


class InstitutionTypeSpec(NamedTuple):
    """One licence class as seeded (docs/sdi.md §1.1)."""

    type_code: str
    display_name: str
    institution_class: str
    return_family: str
    capital_regime: str
    large_exposure_limit_pct: int
    single_obligor_limit_pct: int
    liquidity_binding: bool
    default_modules: tuple[str, ...]


#: The universal-bank module set — every top-level module the platform has.
BANK_MODULES: tuple[str, ...] = (
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
    "irrbb",
    "behavioral",
    "forecasting",
    "ftp",
    "fx",
    "markets",
    "positions",
)
#: The SDI default set (docs/sdi.md §3.2, founder call 2026-08-21): an SDI keeps
#: the ALM engines, IRRBB and Market Data; FX, FTP and trading Positions are
#: bank-only.
SDI_MODULES: tuple[str, ...] = tuple(
    module for module in BANK_MODULES if module not in {"fx", "ftp", "positions"}
)

#: Deposit-taking non-bank licences resolve to the 'sdi' regime (Act 930 s.29
#: capital floor, 15% large-exposure limit, and the LMTD Table 1 ratios that
#: ¶9 would make binding on commencement — the LMTD is an exposure draft posted
#: 19 February 2026 and effective 1 January 2027, so they bind nothing today);
#: the universal bank and a banking-group financial holding company resolve to
#: the 'bank' regime (BoG Capital Requirements Directive, 20% limit, Table 1 as
#: monitoring).
SEED_TYPES: tuple[InstitutionTypeSpec, ...] = (
    InstitutionTypeSpec(
        FALLBACK_TYPE_CODE, "Universal Bank", "bank", "bsd", "crd", 20, 25, False, BANK_MODULES
    ),
    InstitutionTypeSpec(
        "savings_and_loans", "Savings & Loans", "sdi", "sdi", "s29", 15, 25, True, SDI_MODULES
    ),
    InstitutionTypeSpec(
        "finance_house", "Finance House", "sdi", "sdi", "s29", 15, 25, True, SDI_MODULES
    ),
    InstitutionTypeSpec(
        "rural_community_bank",
        "Rural & Community Bank",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        SDI_MODULES,
    ),
    InstitutionTypeSpec(
        "microfinance_bank",
        "Microfinance Institution",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        SDI_MODULES,
    ),
    InstitutionTypeSpec(
        "financial_holding_company",
        "Financial Holding Company",
        "bank",
        "bsd",
        "crd",
        20,
        25,
        False,
        BANK_MODULES,
    ),
    InstitutionTypeSpec(
        "other_rfi",
        "Other Regulated Financial Institution",
        "sdi",
        "sdi",
        "s29",
        15,
        25,
        True,
        SDI_MODULES,
    ),
)


def seed_rows() -> list[dict[str, object]]:
    """The registry as insertable row dicts (``InstitutionType(**row)``)."""
    return [
        {
            "type_code": spec.type_code,
            "display_name": spec.display_name,
            "institution_class": spec.institution_class,
            "return_family": spec.return_family,
            "capital_regime": spec.capital_regime,
            "large_exposure_limit_pct": Decimal(spec.large_exposure_limit_pct),
            "single_obligor_limit_pct": Decimal(spec.single_obligor_limit_pct),
            "liquidity_binding": spec.liquidity_binding,
            "default_modules": list(spec.default_modules),
        }
        for spec in SEED_TYPES
    ]
