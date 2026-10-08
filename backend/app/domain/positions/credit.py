"""On-balance credit exposure rules.

Basis: BoG CRD (June 2018), in force.
Implements: ¶98, ¶106–107, ¶117–119, ¶123–124 and ¶139.
Not: liquidity eligibility, which requires its own evidence.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

DOMESTIC_SOVEREIGN_INSTRUMENTS = frozenset(
    {
        "tbill",
        "tbill_other",
        "gog_bond",
        "gog_bond_other",
        "gog_stock",
        "ggilb",
        "bog_bill",
        "bog_bond",
        "bog_bond_other",
        "bog_other",
    }
)
PSE_INSTRUMENT_CLASSES = {"tor_bond": "public_enterprise", "cocoa_bill": "public_institution"}
PSE_ISSUER_CLASSES = frozenset({"public_institution", "public_enterprise", "soe"})
# Neither a private issuer nor an unknown issuer_class is a sovereign signal.
_BANK_WEIGHTS = {"1": 20, "2": 50, "3": 50, "4": 100, "5": 100, "6": 150, "unrated": 50}
_SHORT_BANK_WEIGHTS = {"1": 20, "2": 20, "3": 20, "4": 50, "5": 50, "6": 150, "unrated": 20}


def attribute_text(attributes: Mapping[str, object], key: str) -> str:
    value = attributes.get(key)
    return value.strip().lower() if isinstance(value, str) else ""


def specific_deductions(attributes: Mapping[str, object], *, non_performing: bool) -> Decimal:
    """BoG CRD (June 2018) ¶98: specific provisions plus interest in suspense.

    An explicit specific provision takes precedence. Otherwise the held ECL on
    a non-performing exposure is specific, using the same classification as
    provision_held. General allowances on performing loans are not deducted.
    Missing amounts grant no deduction; malformed amounts refuse.
    """
    provision = attributes.get("specific_provision_ghs")
    if provision is None and non_performing:
        provision = attributes.get("ecl_provision_ghs")
    return _nonnegative_amount(provision) + _nonnegative_amount(
        attributes.get("interest_in_suspense_ghs")
    )


def _nonnegative_amount(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Specific provisions and suspended interest must be nonnegative amounts.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Specific provisions and suspended interest must be numeric.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(
            "Specific provisions and suspended interest must be finite and nonnegative."
        )
    return amount


def interbank_weight_code(
    attributes: Mapping[str, object],
    *,
    domestic: bool,
    origination: date | None,
    maturity: date | None,
) -> str | None:
    """BoG CRD (June 2018) ¶123–124: ERG and ORIGINAL three-month maturity.

    An ERG is the bank's ingested assessment mapped under ¶101–105. Unknown
    grades refuse; an absent grade is unrated. Remaining maturity never grants
    the preferential short-term weight.
    """
    raw_grade = attributes.get("external_rating_grade")
    grade = "unrated" if raw_grade is None else str(raw_grade).strip().lower()
    short = False
    if domestic and origination is not None and maturity is not None:
        month_index = origination.year * 12 + origination.month - 1 + 3
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        three_months = date(year, month, min(origination.day, monthrange(year, month)[1]))
        short = origination <= maturity <= three_months
    weight = (_SHORT_BANK_WEIGHTS if short else _BANK_WEIGHTS).get(grade)
    return None if weight is None else f"RW{weight}"
