"""The four recognised optional position attributes, and their discipline (pure).

``docs/bi.md`` §Phase 5 asks for four optional Data Engine fields so a bank can
break its book down by the officer who owns a facility, the channel it came
through, the account's own status, and how much of it is overdue:

===================  ===========================================================
``officer_id``       the bank's own code for the relationship / credit officer
``channel``          the origination or servicing channel (closed vocabulary)
``account_status``   the account's lifecycle state (closed vocabulary)
``arrears_amount``   the overdue portion of ``balance``, same currency
===================  ===========================================================

They ride the ``attributes`` bag (``docs/API_INTEGRATION.md`` §3.4) like every
other optional per-position statement — ``branch_id``, ``sector``,
``ecl_provision_ghs`` — so they need no column and no migration, and a bank that
sends none of them is unaffected by everything in this module.

**Why they are normalised at all.** The bag is free-form by design, and an
optional field accepted with no discipline arrives as free text from three banks
in three shapes: ``"Mobile Money"``, ``"MOMO"`` and ``"momo"`` would be three
channels, and ``"1,250.00"`` would be a channel-shaped string rather than money.
So the two closed vocabularies are resolved to ONE spelling, the open identifier
is trimmed and bounded, and the money figure is parsed exactly. What cannot be
resolved is **dropped and reported** — never stored as free text, and never
guessed at. The report names the position, the key, the value the source sent and
the vocabulary it must come from, which is what an integration engineer needs.

**Why a bad value does not kill the record.** ``deposit_account_type`` and
``ifrs9_stage`` are typed canonical fields: an unrecognised value fails
translation and the position never lands. That is right for a field a return
depends on. It is wrong here — deleting a facility from the balance sheet because
its ``channel`` was misspelled would be a much larger error than the misspelling.
So the value is dropped, the key is absent (which reads as "not stated", never as
a default), and the finding's severity is per-institution configuration like
every other validation rule (``optional_position_attributes``, default WARNING).

**Missing is never zero.** A dropped or absent ``arrears_amount`` means the
position has no stated arrears — NOT a position with no arrears. Nothing in this
module writes a zero, and no consumer may read absence as one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.ingestion.contracts import CanonicalRecords, PositionData

#: The origination / servicing channel a position arrived or is serviced through.
#: Lower snake_case, like every other enumerated ``attributes`` vocabulary
#: (``payroll_deduction``, ``public_institution``, the loan-event subtypes) and
#: unlike the canonical UPPER_SNAKE enums, which are translation targets.
#:
#: ``other`` is deliberate: without an honest home for a channel this list does
#: not name, a bank smuggles its own token in and collects a warning every night.
CHANNELS: tuple[str, ...] = (
    "branch",
    "agent",
    "atm",
    "pos",
    "mobile_app",
    "ussd",
    "internet_banking",
    "mobile_money",
    "call_centre",
    "direct_sales",
    "partner",
    "api",
    "other",
)

#: Spellings resolved onto :data:`CHANNELS`. Every entry must be unambiguous —
#: a bare ``mobile`` is NOT here, because it could mean the app, the wallet or
#: USSD, and picking one would be inventing the bank's meaning rather than
#: reading it. ``pos_terminal`` maps to ``pos``; a bare ``card`` does not, since
#: a card transacts at an ATM as readily as at a terminal.
CHANNEL_ALIASES: dict[str, str] = {
    "teller": "branch",
    "counter": "branch",
    "over_the_counter": "branch",
    "otc": "branch",
    "branch_teller": "branch",
    "agency": "agent",
    "agent_banking": "agent",
    "pos_terminal": "pos",
    "app": "mobile_app",
    "mobile_banking": "mobile_app",
    "online": "internet_banking",
    "internet": "internet_banking",
    "web": "internet_banking",
    "e_banking": "internet_banking",
    "ebanking": "internet_banking",
    "momo": "mobile_money",
    "mobile_wallet": "mobile_money",
    "wallet": "mobile_money",
    "call_center": "call_centre",
    "contact_centre": "call_centre",
    "contact_center": "call_centre",
    "dsa": "direct_sales",
    "direct_sales_agent": "direct_sales",
    "field_sales": "direct_sales",
    "third_party": "partner",
    "host_to_host": "api",
    "h2h": "api",
}

#: The account's own lifecycle state. One vocabulary spans both sides of the
#: book on purpose: ``dormant`` is a deposit concept and ``written_off`` a loan
#: one, but a single key that a bank fills for every account is worth more than
#: two keys it fills for half of one.
#:
#: ``inactive`` and ``dormant`` are kept apart because core systems keep them
#: apart: dormancy is a rule the bank has APPLIED (with its own consequences for
#: unclaimed balances), while an inactive account is merely quiet. Collapsing
#: them would force a bank to state something it does not mean.
ACCOUNT_STATUSES: tuple[str, ...] = (
    "active",
    "inactive",
    "dormant",
    "blocked",
    "closed",
    "matured",
    "written_off",
    "other",
)

#: Spellings resolved onto :data:`ACCOUNT_STATUSES`. ``suspended`` is NOT here:
#: on a loan it commonly means interest has been suspended (the platform's own
#: ``interest_in_suspense_ghs``), not that the account cannot transact.
ACCOUNT_STATUS_ALIASES: dict[str, str] = {
    "open": "active",
    "live": "active",
    "operative": "active",
    "frozen": "blocked",
    "lien": "blocked",
    "write_off": "written_off",
    "writeoff": "written_off",
    "written": "written_off",
    "charged_off": "written_off",
    "charge_off": "written_off",
}

#: Length bound on the open identifier. Generous for any staff code, narrow
#: enough to catch a whole customer record pasted into the column; it is also
#: the width the mart column would take (``branch_code`` is ``String(120)``).
OFFICER_ID_MAX_LENGTH = 120

#: The recognised keys, in the spelling they are stored under.
OPTIONAL_POSITION_ATTRIBUTE_KEYS: tuple[str, ...] = (
    "officer_id",
    "channel",
    "account_status",
    "arrears_amount",
)

#: Position types an arrears figure is meaningful on: a position is in arrears
#: only if something was contractually due and not paid. Stated elsewhere it is
#: reported by ``position_attribute_consistency`` (and still stored — the bank
#: may mean something we have not modelled; we simply do not aggregate it).
ARREARS_POSITION_TYPES: frozenset[str] = frozenset({"LOAN"})


@dataclass(frozen=True, slots=True)
class AttributeProblem:
    """One optional attribute value that could not be used, and why.

    ``detail`` is the message an integration engineer reads in the batch report:
    it names the key, quotes what was sent, says what is accepted and states that
    the value was not stored. The validation rule
    (``optional_position_attributes``) turns each of these into a ``Finding`` at
    the institution's configured severity.
    """

    source_reference: str
    source_locator: str
    key: str
    detail: str


def _canonical_token(value: Any) -> str:
    """A closed-vocabulary token: case-folded, separators unified, runs collapsed."""
    text = str(value).strip().casefold()
    for separator in (" ", "-", ".", "/"):
        text = text.replace(separator, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _vocabulary_message(key: str, raw: Any, vocabulary: tuple[str, ...]) -> str:
    return (
        f"{key} {str(raw)!r} is not a recognised value; send one of: "
        f"{', '.join(sorted(vocabulary))} (matched case-insensitively, and "
        f"spaces or hyphens are read as underscores). The value was not stored."
    )


def _resolve_closed(
    key: str,
    raw: Any,
    vocabulary: tuple[str, ...],
    aliases: Mapping[str, str],
) -> tuple[str | None, str | None]:
    token = _canonical_token(raw)
    if not token:
        return None, (
            f"{key} was sent empty; omit the key instead — an absent attribute "
            f"reads as 'not stated', while a blank one states nothing at all."
        )
    resolved = aliases.get(token, token)
    if resolved not in vocabulary:
        return None, _vocabulary_message(key, raw, vocabulary)
    return resolved, None


def _resolve_officer_id(raw: Any) -> tuple[str | None, str | None]:
    """Trim and bound the officer code; case and spelling stay the bank's own.

    Not case-folded on purpose: it is matched against the bank's own staff
    register, and folding it would make the platform's idea of the key differ
    from the bank's — the same reason ``branch_id`` is carried verbatim.
    """
    text = " ".join(str(raw).split())
    if len(text) > OFFICER_ID_MAX_LENGTH:
        return None, (
            f"officer_id is {len(text)} characters long, above the "
            f"{OFFICER_ID_MAX_LENGTH}-character limit; send the officer's code "
            f"as your core system holds it, not a name-and-notes field. The "
            f"value was not stored."
        )
    if not text.isprintable():
        return None, (
            "officer_id contains control characters; send the officer's code as "
            "plain text. The value was not stored."
        )
    return text, None


def _resolve_arrears_amount(raw: Any) -> tuple[str | None, str | None]:
    """Parse the overdue amount exactly, and store it as a decimal string.

    Money is kept as an exact decimal STRING in the bag because JSON numbers are
    IEEE doubles in most clients and a round-trip through one is not exact; every
    existing reader of a money attribute already parses a string.
    """
    text = str(raw).strip().replace(",", "")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, (
            f"arrears_amount {str(raw)!r} is not a number; send a JSON number or "
            f"a plain numeric string, stated in the position's own currency like "
            f"balance. The value was not stored."
        )
    if not amount.is_finite():
        return None, (
            f"arrears_amount {str(raw)!r} is not a finite number. The value was not stored."
        )
    if amount < 0:
        return None, (
            f"arrears_amount {str(raw)!r} is negative; arrears is the overdue "
            f"amount as a positive figure. Send 0 only if you mean the position "
            f"is genuinely up to date, and omit the key if you do not know. The "
            f"value was not stored."
        )
    return str(amount), None


def normalize_attributes(
    attributes: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
    """One position's bag, with the four recognised keys normalised.

    Returns the new bag and ``(key, detail)`` for every value that could not be
    used. Keys are matched case-insensitively and stored under their canonical
    spelling (``"Officer_ID"`` → ``"officer_id"``), the documented convention for
    the ``attributes`` vocabulary. Every OTHER key passes through untouched: this
    function knows about four fields and must never reshape the rest of the bag.
    """
    normalized: dict[str, Any] = {}
    problems: list[tuple[str, str]] = []
    recognized = {key.casefold(): key for key in OPTIONAL_POSITION_ATTRIBUTE_KEYS}
    for raw_key, raw_value in attributes.items():
        key = recognized.get(str(raw_key).strip().casefold())
        if key is None:
            normalized[raw_key] = raw_value
            continue
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            # Absent and blank are the same statement, and neither is a defect:
            # a CSV row simply has no value in that column.
            continue
        value, problem = _resolve(key, raw_value)
        if problem is not None:
            problems.append((key, problem))
            continue
        normalized[key] = value
    return normalized, tuple(problems)


#: JSON types that cannot be any of the four fields. Checked once for all four
#: rather than per resolver: a nested object stringifies into something a
#: vocabulary check would then report as an unrecognised VALUE, which tells an
#: integration engineer to fix the wrong thing.
_UNUSABLE_TYPES = bool | dict | list


def _resolve(key: str, raw_value: Any) -> tuple[Any, str | None]:
    if isinstance(raw_value, _UNUSABLE_TYPES):
        expected = "a number" if key == "arrears_amount" else "a string"
        return None, (
            f"{key} must be {expected}; received a JSON "
            f"{type(raw_value).__name__}. The value was not stored."
        )
    if key == "channel":
        return _resolve_closed("channel", raw_value, CHANNELS, CHANNEL_ALIASES)
    if key == "account_status":
        return _resolve_closed(
            "account_status", raw_value, ACCOUNT_STATUSES, ACCOUNT_STATUS_ALIASES
        )
    if key == "officer_id":
        return _resolve_officer_id(raw_value)
    return _resolve_arrears_amount(raw_value)


def normalize_positions(records: CanonicalRecords) -> tuple[AttributeProblem, ...]:
    """Normalise every translated position's optional attributes, in place.

    Called by the ingestion orchestrator between translation and validation, so
    there is ONE seam for every source — Excel/CSV upload, API push, and each
    core-banking adapter — and no adapter carries a copy of the vocabulary.
    Assignment to ``PositionData.attributes`` is the single mutation here; it is
    deliberate, because validation and persistence must both see the normalised
    bag and copying the whole record set to change one dict would not.
    """
    problems: list[AttributeProblem] = []
    for position in records.positions:
        if not position.attributes:
            continue
        normalized, found = normalize_attributes(position.attributes)
        if found:
            problems.extend(
                AttributeProblem(
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    key=key,
                    detail=detail,
                )
                for key, detail in found
            )
        if normalized != position.attributes:
            position.attributes = normalized
    return tuple(problems)


def stated_arrears_amount(position: PositionData) -> Decimal | None:
    """The normalised arrears figure, or ``None`` when the position states none.

    ``None`` is "not stated" and NEVER zero: a position with no arrears figure is
    not a position with no arrears.
    """
    value = position.attributes.get("arrears_amount")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
