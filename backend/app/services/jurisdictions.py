"""Jurisdiction resolution for services — the first two links of the policy chain.

Country identity (regulator names, currency, locale) is DATA: it lives in the
global ``jurisdictions`` registry and resolves through the bank's
``jurisdiction_code``. Services must call these helpers instead of hardcoding
"Bank of Ghana" / "BoG" / "GHS" / "GH".

Two distinct classes of helper live here, and the difference is deliberate:

* **Display fallbacks are allowed.** ``regulator_name``/``regulator_short`` fall
  back to a neutral label so a narrative sentence never reads "Bank of Ghana" for
  a Nigerian bank. Getting a label slightly generic is harmless.
* **Identity fallbacks are NOT allowed.** ``base_currency`` and
  ``jurisdiction_code`` have no fallback at all. ``banks.currency`` and
  ``banks.jurisdiction_code`` are NOT NULL and carry no defaults precisely
  because they used to default independently to ``"GHS"``/``"GH"`` and could
  silently disagree — a bank created with ``jurisdiction_code="NG"`` kept
  reporting in cedis. Substituting either here re-creates that trap one layer
  further down (enterprise audit 2026-08-20 §6).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.policy import PolicyUnresolvedError, policy_unresolved
from app.models import Bank, Jurisdiction

FALLBACK_REGULATOR_NAME = "the banking regulator"
FALLBACK_REGULATOR_SHORT = "Regulator"


def get_jurisdiction(db: Session, bank: Bank) -> Jurisdiction | None:
    """The bank's registry row, or ``None`` — display-side lookup."""
    return db.get(Jurisdiction, bank.jurisdiction_code)


def regulator_name(db: Session, bank: Bank) -> str:
    """Full central-bank name for display, e.g. "Bank of Ghana"."""
    row = get_jurisdiction(db, bank)
    return row.central_bank_name if row is not None else FALLBACK_REGULATOR_NAME


def regulator_short(db: Session, bank: Bank) -> str:
    """Short regulator form for display, e.g. "BoG"."""
    row = get_jurisdiction(db, bank)
    return row.regulator_short if row is not None else FALLBACK_REGULATOR_SHORT


def base_currency(bank: Bank) -> str:
    """The bank's reporting currency, normalised — e.g. "GHS", "NGN".

    There is deliberately no fallback. ``banks.currency`` is NOT NULL and
    carries no default, so an unset value means the creation path skipped a
    required decision; substituting one here is how a Nigerian bank ends up
    reporting in cedis. Narrative and unit labels must resolve through this
    rather than writing a currency literal.
    """
    code = (bank.currency or "").strip().upper()
    if not code:
        msg = (
            f"Bank {bank.id} has no reporting currency. It is required at "
            "creation and must match the jurisdiction's currency_code."
        )
        raise ValueError(msg)
    return code


def jurisdiction_code(bank: Bank) -> str:
    """The bank's jurisdiction code, normalised — e.g. "GH", "NG". FAIL CLOSED.

    The counterpart of :func:`base_currency` for link 1 of the policy chain, and
    the replacement for every ``(bank.jurisdiction_code or "GH")`` in the
    codebase. A blank code must never resolve to Ghana: it would silently select
    Ghana's entire regulatory parameter set — CAR floor, provisioning grid, DPD
    boundaries, LMTD floors — for an institution licensed somewhere else.
    """
    code = (bank.jurisdiction_code or "").strip().upper()
    if not code:
        raise PolicyUnresolvedError(
            policy_unresolved(
                "jurisdiction_code",
                reason=(
                    f"Bank {bank.id} has no jurisdiction_code. It is required at creation "
                    "and carries no default — an unset value means the creation site "
                    "skipped a required decision, not that the institution is Ghanaian. "
                    "No regulatory parameter set can be selected without it."
                ),
                items=(f"bank:{bank.id}", "field:jurisdiction_code"),
                context={"bank_id": bank.id, "organization_id": bank.organization_id},
            )
        )
    return code


def require_jurisdiction(db: Session, bank: Bank) -> Jurisdiction:
    """The bank's jurisdiction registry row (FAIL CLOSED).

    Used when the regulator identity itself is load-bearing — building a policy
    scope, naming the regulator on a filed artifact. Display paths keep using
    :func:`regulator_name` / :func:`regulator_short` and their neutral fallbacks.
    """
    code = jurisdiction_code(bank)
    row = db.get(Jurisdiction, code)
    if row is None:
        raise PolicyUnresolvedError(
            policy_unresolved(
                "jurisdiction",
                reason=(
                    f"Bank {bank.id} jurisdiction_code {code!r} is not in the jurisdictions "
                    "registry, so its regulator, currency and locale cannot be resolved. "
                    "Register the jurisdiction rather than defaulting to another country's."
                ),
                items=(f"bank:{bank.id}", f"jurisdiction:{code}"),
                context={"bank_id": bank.id, "jurisdiction_code": code},
            )
        )
    return row
