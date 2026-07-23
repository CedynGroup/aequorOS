"""Jurisdiction resolution for services.

Country identity (regulator names, currency, locale) is DATA: it lives in the
global ``jurisdictions`` registry and resolves through the bank's
``jurisdiction_code``. Services must call these helpers instead of hardcoding
"Bank of Ghana" / "BoG" — the neutral fallbacks below only cover a code with
no registry row.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Bank, Jurisdiction

FALLBACK_REGULATOR_NAME = "the banking regulator"
FALLBACK_REGULATOR_SHORT = "Regulator"


def get_jurisdiction(db: Session, bank: Bank) -> Jurisdiction | None:
    return db.get(Jurisdiction, bank.jurisdiction_code)


def regulator_name(db: Session, bank: Bank) -> str:
    """Full central-bank name for display, e.g. "Bank of Ghana"."""
    row = get_jurisdiction(db, bank)
    return row.central_bank_name if row is not None else FALLBACK_REGULATOR_NAME


def regulator_short(db: Session, bank: Bank) -> str:
    """Short regulator form for display, e.g. "BoG"."""
    row = get_jurisdiction(db, bank)
    return row.regulator_short if row is not None else FALLBACK_REGULATOR_SHORT
