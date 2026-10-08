"""On-balance credit exposure rules.

Basis: BoG CRD (June 2018), in force.
Implements: ¶98, ¶106–107, ¶117–122, ¶123–124 and ¶139.
Not: liquidity eligibility, which requires its own evidence.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from app.domain.positions.families import (
    LOAN_CATEGORY_MAP,
    PAST_DUE_CATEGORY,
    unclassified_category,
)

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


class CreditPosition(Protocol):
    @property
    def attributes(self) -> Mapping[str, object]: ...

    @property
    def position_type(self) -> str: ...

    @property
    def counterparty_type(self) -> str | None: ...

    @property
    def counterparty_country(self) -> str | None: ...

    @property
    def counterparty_resident(self) -> bool | None: ...

    @property
    def product_code(self) -> str | None: ...

    @property
    def regulatory_category(self) -> str | None: ...

    @property
    def ifrs9_stage(self) -> int | None: ...

    @property
    def origination_date(self) -> date | None: ...

    @property
    def contractual_maturity(self) -> date | None: ...


def _public_class(row: CreditPosition) -> str:
    key = "borrower_class" if row.position_type == "LOAN" else "issuer_class"
    declared = attribute_text(row.attributes, key)
    if row.position_type == "LOAN" and declared:
        return declared if declared in PSE_ISSUER_CLASSES else ""
    return PSE_INSTRUMENT_CLASSES.get(attribute_text(row.attributes, "instrument")) or declared


def sovereign_evidence(row: CreditPosition, sovereign_names: tuple[str, ...]) -> bool:
    attributes = row.attributes
    if _public_class(row) or row.counterparty_type in (
        "GOVERNMENT_ENTITY",
        "MULTILATERAL_DEV_BANK",
    ):
        return False
    return (
        row.counterparty_type in ("SOVEREIGN", "CENTRAL_BANK")
        or attribute_text(attributes, "instrument") in DOMESTIC_SOVEREIGN_INSTRUMENTS
        or any(
            token in f"{row.product_code or ''} {row.regulatory_category or ''}".upper()
            for token in ("TBILL", "T-BILL", "GOG", "GOVT", "GOVERNMENT", "TREASURY", "SOVEREIGN")
        )
        or attribute_text(attributes, "issuer") in sovereign_names
    )


def public_debt_evidence(row: CreditPosition, sovereign_names: tuple[str, ...]) -> bool:
    return (
        sovereign_evidence(row, sovereign_names)
        or _public_class(row) in PSE_ISSUER_CLASSES
        or row.counterparty_type in ("GOVERNMENT_ENTITY", "MULTILATERAL_DEV_BANK")
    )


def public_credit_class(
    row: CreditPosition,
    *,
    foreign: bool,
    sovereign_names: tuple[str, ...],
    domestic_country: str | None,
) -> tuple[str, str | None]:
    """BoG CRD (June 2018) ¶106–122: counterparty evidence independent of liquidity."""
    attributes = row.attributes
    instrument = attribute_text(attributes, "instrument")
    issuer_class = _public_class(row)
    country = (row.counterparty_country or "").strip().upper()
    domestic_code = (domestic_country or "").strip().upper()
    foreign_domicile = row.counterparty_resident is False or bool(
        country and country != domestic_code
    )
    if issuer_class in PSE_ISSUER_CLASSES:
        domestic_pse = not foreign_domicile and (
            (country and country == domestic_code)
            or (not country and row.counterparty_resident is True)
            or (not country and instrument in PSE_INSTRUMENT_CLASSES)
        )
        weight = 50 if issuer_class == "public_institution" else 100
        code = f"RW{weight}+RW20" if foreign else f"RW{weight}"
        return f"pse_{issuer_class}", code if domestic_pse else None
    if row.counterparty_type in ("GOVERNMENT_ENTITY", "MULTILATERAL_DEV_BANK"):
        return "unclassified_public_sector", None
    product = (row.product_code or "").upper().split(".")
    domestic = (
        instrument in DOMESTIC_SOVEREIGN_INSTRUMENTS
        or attribute_text(attributes, "issuer") in sovereign_names
        or bool({"TBILL", "GOG", "BOG"}.intersection(product))
        or (row.regulatory_category or "").upper() == "SOVEREIGN_LOCAL_CCY"
    )
    if domestic and (foreign_domicile or issuer_class):
        return "unclassified_issuer", None
    if domestic:
        issuer_role = ""
        if (
            instrument.startswith("bog_")
            or "BOG" in product
            or row.counterparty_type == "CENTRAL_BANK"
        ):
            issuer_role = "bog"
        elif (
            instrument in DOMESTIC_SOVEREIGN_INSTRUMENTS
            or {"TBILL", "GOG"}.intersection(product)
            or row.counterparty_type == "SOVEREIGN"
        ):
            issuer_role = "gog"
        category = f"domestic_sovereign:{issuer_role}" if issuer_role else "domestic_sovereign"
        return category, "RW20" if foreign else "RW0"
    if sovereign_evidence(row, sovereign_names):
        return "unclassified_sovereign", None
    return "other_securities", "RW100"


def capital_credit_class(
    row: CreditPosition,
    *,
    foreign: bool,
    sovereign_names: tuple[str, ...] = (),
    domestic_country: str | None = None,
) -> tuple[str, str | None]:
    """BoG CRD (June 2018) ¶106–124, ¶139: one classifier for capital and stress."""
    if row.position_type == "SECURITY_HOLDING":
        category, code = public_credit_class(
            row, foreign=foreign, sovereign_names=sovereign_names, domestic_country=domestic_country
        )
        return f"securities:{category}", code
    if row.position_type == "LOAN" and row.ifrs9_stage == 3:
        return PAST_DUE_CATEGORY
    if public_debt_evidence(row, sovereign_names):
        category, code = public_credit_class(
            row, foreign=foreign, sovereign_names=sovereign_names, domestic_country=domestic_country
        )
        prefix = "loans" if row.position_type == "LOAN" else "interbank"
        return f"{prefix}:{category}", code
    if (
        row.counterparty_type in ("BANK_OECD", "BANK_NON_OECD")
        or row.position_type == "INTERBANK_PLACEMENT"
    ):
        code = (
            interbank_weight_code(
                row.attributes,
                domestic=not foreign,
                origination=row.origination_date,
                maturity=row.contractual_maturity,
            )
            if row.counterparty_type in (None, "BANK_OECD", "BANK_NON_OECD")
            else None
        )
        return "loans:banks" if row.position_type == "LOAN" else "interbank", code
    category, code = LOAN_CATEGORY_MAP.get(
        (row.regulatory_category or "").upper(),
        (unclassified_category(row.regulatory_category), None),
    )
    if category == "sme_retail" and foreign:
        code = "RW100+RW20"
    return category, code
