"""``gl_segment_balances`` — the general ledger broken down by branch.

``canonical_gl_accounts`` is the chart of accounts CARRYING a balance: one row
per ``(account_code, as_of_date)``, with ``name`` / ``account_class`` /
``parent_account_id`` describing the *account*. General ledger by branch is a
different grain — ``(account, branch, date)`` — and
``uq_canonical_gl_accounts_current`` forbids expressing it there: a second row
for the same code and date, differing only in an ``attributes.branch_id``,
collides with the first. A position is individually identified
(``source_reference``), so its branch is an ATTRIBUTE; a ledger account is
identified by its code, so its branch is a second KEY. Hence a dataset of its
own rather than a wider ``gl_account`` record.

**The branch is not parsed out of the account code, and must not be.** A bank
whose chart of accounts happens to encode a branch inside ``account_code`` has
promised nothing about where: the public contract (docs/API_INTEGRATION.md §3.1)
documents the field as "GL account code" and no more. Temenos T24 — the
platform's only core-banking adapter family — proves the general case: its
``GL_BALANCES`` domain takes ``glCode`` as the account and the branch as the
``coCode`` SELECTION parameter, and the very same catalog maps ``coCode`` onto
``branch_id`` on positions. The branch is a dimension of the ledger, not a
substring of its key. Reading it out of the key would also give one identifier
two grammars at once, since ``gl_mapping_bsd7`` already resolves a BSD7 P&L item
by LONGEST MATCHING PREFIX of the same string — and it is the same class of
defect as the GL cash classifier that tested the literal token ``"bog"`` and
dropped a whole tenant's central-bank balances out of HQLA.

Shape and conventions
---------------------
**P&L accounts.** The figures are fiscal-year-to-date balances of INCOME and
EXPENSE accounts, on the same trial-balance convention and with the same sign as
the ``balance`` the bank already sends on a ``gl_account`` record
(``app/domain/gl/pl_mapping.py``). A row naming an account the ledger does not
carry as a P&L account at that date has nothing to reconcile against and is
reported rather than counted (see below).

**One reporting date per push**, batch ``as_of_date`` = that date, like
``interest_accruals``: a reader takes the latest batch on/before the period end,
so a batch must carry that date's WHOLE branch breakdown.

**The breakdown may be partial, and says so.** Nothing requires a bank to
allocate every cedi of every account to a branch; head-office lines frequently
are not allocated at all. The branch mart therefore carries an explicit residual
row per (account, month) holding ``institution_ytd − Σ reported_ytd``, so the
branch breakdown sums to the institution's ledger by construction and an
unallocated remainder is a labelled line rather than a missing one.
:data:`RESIDUAL_BRANCH_ID` is that row's branch key and is RESERVED — a bank that
sent it would be claiming to have computed the platform's own residual, so
:func:`validate_gl_segment_row` refuses it.

Docs: docs/data_engine/datasets/gl_segment_balances.md.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from . import ReferenceSchema, register

#: The branch key of the computed unallocated remainder. Reserved: the platform
#: writes it, a bank may not send it. Double-underscored on both sides so it
#: cannot collide with a real ``business_unit_id`` (``BR-001``, a cost centre, a
#: T24 company code) by accident.
RESIDUAL_BRANCH_ID = "__UNALLOCATED__"

#: What the residual row is called wherever a branch name is shown. Not a
#: branch, so it carries no region and no outlet.
RESIDUAL_BRANCH_NAME = "Unallocated (not attributed to a branch)"

SCHEMA = register(
    ReferenceSchema(
        kind="gl_segment_balances",
        description=(
            "General ledger by branch: the fiscal-year-to-date balance of one P&L account for "
            "one business unit at the reporting date, on the same convention as the account's "
            "own institution-level balance"
        ),
        grain=(
            "one row per (as_of_date, gl_account_code, branch_id, currency); one reporting date "
            "per push (as_of_date = the reporting date), the whole breakdown in that batch"
        ),
        required=("as_of_date", "gl_account_code", "branch_id", "ytd_balance"),
        optional=("currency", "gl_account_name", "branch_name", "notes"),
        numeric=("ytd_balance",),
        dates=("as_of_date",),
    )
)


def _iso_date_problem(field: str, value: object) -> str | None:
    """``None`` when ``value`` is an ISO date, else the problem to report.

    ``ReferenceSchema.dates`` is a template/documentation hint — ``validate_row``
    never reads it — so a date this dataset KEYS on has to be checked here. A
    malformed reporting date would not fail anything loudly: the row would simply
    never match a ledger month and would be reported as unreconcilable long after
    the push succeeded.
    """
    text = str(value or "").strip()
    if not text:
        return None  # absence is the required-field check's business, not this one
    try:
        date.fromisoformat(text)
    except ValueError:
        return f"field '{field}' must be an ISO date YYYY-MM-DD (got {value!r})"
    return None


def validate_gl_segment_row(row: dict) -> list[str]:
    """Schema problems plus this dataset's own three rules.

    1. ``as_of_date`` parses as an ISO date (see :func:`_iso_date_problem`).
    2. ``branch_id`` is not :data:`RESIDUAL_BRANCH_ID`: that key belongs to the
       computed remainder, and a pushed row wearing it would be double-counted
       against the residual the platform derives for the same account.
    3. ``currency``, when given, is a three-letter code — blank means the bank's
       own reporting currency, exactly as an unstated currency does on a
       ``gl_account`` record (BSD7's Domestic rule). A two- or four-letter value
       is a mistake that would silently split one account into two mart rows.
    """
    problems = SCHEMA.validate_row(row)
    date_problem = _iso_date_problem("as_of_date", row.get("as_of_date"))
    if date_problem is not None:
        problems.append(date_problem)
    branch_id = str(row.get("branch_id") or "").strip()
    if branch_id == RESIDUAL_BRANCH_ID:
        problems.append(
            f"field 'branch_id' must not be {RESIDUAL_BRANCH_ID!r}: that key is reserved for the "
            "unallocated remainder the platform computes from this dataset"
        )
    currency = str(row.get("currency") or "").strip()
    if currency and (len(currency) != 3 or not currency.isalpha()):
        problems.append(
            f"field 'currency' must be a three-letter ISO 4217 code or blank for the reporting "
            f"currency (got {row.get('currency')!r})"
        )
    return problems


# Bound after the function exists, for the reason recorded in
# ``business_units.py``: ``schema_for('gl_segment_balances')`` must return the
# schema WITH its rules, because the ingestion path asks ``problems_for`` and
# an unbound validator means the declarative half only runs (audit A7-07 / H-027).
SCHEMA = register(dataclasses.replace(SCHEMA, row_validator=validate_gl_segment_row))
