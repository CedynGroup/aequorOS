"""Institution-type resolution for services (docs/sdi.md §1.2).

The institution discriminator is DATA: it lives in the global
``institution_types`` registry and resolves through the bank's typed
``institution_type`` code, exactly as country identity resolves through
``jurisdiction_code`` (``app/services/jurisdictions.py``). Services and
calculation engines that need the derived regime — the coarse
``institution_class`` ('bank'|'sdi'), the return family, the exposure limits —
call these helpers instead of hardcoding ``"bank"``.

Fail-loud discipline (mirrors ``jurisdictions.base_currency``): a derived
regulatory attribute is never silently defaulted. If the bank's own type is not
in the registry the resolver falls back to the named ``universal_bank`` row, and
if EVEN that row is missing it raises — the registry seed migration
(``202608190018_institution_types_registry``) is a hard prerequisite, not an
optional convenience.

NOTE (Phase A): these resolvers are the extension point for later SDI phases
(class-keyed thresholds, return-family selection, module gating). Phase A wires
NO calculation to branch on them yet — it only makes the discriminator resolve.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Bank, InstitutionType

#: The named fallback licence class. A bank whose ``institution_type`` code has
#: no registry row resolves to this row rather than a silent hardcoded regime.
FALLBACK_TYPE_CODE = "universal_bank"


def get_type(db: Session, bank: Bank) -> InstitutionType:
    """Resolve the bank's institution-type registry row (fail-loud).

    Returns the row for the bank's own ``institution_type`` when present, else
    the ``universal_bank`` fallback row. Raises ``ValueError`` only when neither
    exists — an unseeded registry — because a derived regulatory attribute must
    never be substituted out of thin air.
    """
    code = (bank.institution_type or "").strip()
    row = db.get(InstitutionType, code) if code else None
    if row is not None:
        return row
    fallback = db.get(InstitutionType, FALLBACK_TYPE_CODE)
    if fallback is None:
        msg = (
            f"Bank {bank.id} institution_type {bank.institution_type!r} is not in the "
            f"institution_types registry and the {FALLBACK_TYPE_CODE!r} fallback row is "
            "missing. The registry is seeded by its migration and is a hard prerequisite "
            "for resolving the institution discriminator."
        )
        raise ValueError(msg)
    return fallback


def institution_class(db: Session, bank: Bank) -> str:
    """The coarse regime axis ('bank' | 'sdi') — the value the threshold and
    (future) capital registers key on. Derived from the licence class."""
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
