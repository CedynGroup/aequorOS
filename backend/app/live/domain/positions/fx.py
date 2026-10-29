"""The currency universe of the existing on-balance and FX-hedge NOP book.

Basis: Notice BG/FMD/2026/07 (in force).
Implements: ¶1(a)–(b): history availability cannot exclude a currency.
Not: the separate NOF, long-position or contingent-exposure changes.
"""

from collections.abc import Collection, Mapping
from decimal import Decimal

from app.domain.authority.outcomes import NotComputable, OutcomeDetail, OutcomeState, outcome

_ZERO = Decimal("0")
_NOP_METRIC_ID = "nop_ghs"


FX_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "INTERBANK_PLACEMENT")
FX_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING")
FX_POSITION_TYPES = (*FX_ASSET_TYPES, *FX_LIABILITY_TYPES, "FX_HEDGE")


def position_currencies(
    position_type: str, currency: str, attributes: Mapping[str, object], base_currency: str
) -> set[str]:
    """Foreign currencies the position contributes to, independently of rates."""
    currencies: set[str]
    if position_type == "FX_HEDGE":
        currencies = {
            str(attributes.get("sell_currency") or currency).strip().upper(),
            str(attributes.get("buy_currency") or base_currency).strip().upper(),
        }
    elif position_type in (*FX_ASSET_TYPES, *FX_LIABILITY_TYPES):
        currencies = {currency.strip().upper()}
    else:
        currencies = set()
    return currencies - {base_currency.strip().upper(), ""}


def require_currency_coverage(expected: Collection[str], represented: Collection[str]) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): refuse a partial NOP currency universe."""
    missing = sorted(set(expected) - set(represented))
    if missing:
        raise NotComputable(
            *(
                outcome(
                    OutcomeState.DATA_QUALITY_BLOCK,
                    metric_id="nop_ghs",
                    reason=(
                        f"The FX basis omits {currency}. Re-derive official FX facts and run "
                        "a complete baseline FX analysis before calculating or filing NOP. "
                        "Missing return history must not remove a currency from the limits."
                    ),
                    items=(f"currency:{currency}",),
                    context={"currency": currency},
                )
                for currency in missing
            )
        )


def spot_or_none(raw: object) -> Decimal | None:
    """The fact's revaluation rate, or ``None`` when the row carries none.

    ``fact_derivation._resolve_spot`` never invents a rate (audit 2026-08-22
    D-13): with no ingested spot and none implied by the position book it
    returns ``None``, and the writer stores an EMPTY ``spot_ghs``. Absence is
    therefore a real state this reader must handle rather than a corrupt row —
    but it is NOT, on its own, evidence that a position is unstated. See
    :func:`fx_position_refusal` for what absence does and does not license.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() else None
    except ArithmeticError:
        return None


def fx_position_refusal(
    currency: str,
    net_ccy: Decimal,
    net_ghs: Decimal,
    spot: Decimal | None,
    *,
    rate_required: bool = False,
) -> OutcomeDetail | None:
    """The reason this currency's net open position cannot be filed, or ``None``.

    Missing valuation evidence and contradictory conversions are refused;
    no tolerance threshold is invented, because a booked-rate-vs-period-end-spot
    drift is legitimate:

    1. A non-zero currency net carried at exactly zero in the reporting unit.
       That is the D-21 signature — a position without ``balance_ghs`` still
       contributes to the currency leg but not the reporting-currency leg.
       Reporting zero would claim that the exposure does not exist.
    2. The currency net and its reporting-currency equivalent disagreeing in
       DIRECTION. No positive rate turns a long into a short; a whole book whose
       legs are converted inconsistently enough to flip the sign is not a
       revaluation difference, it is a broken conversion.
    3. No rate for an exposure or a hedge delta requiring conversion, even if
       netting makes both reported nets zero. The run must refuse rather than
       count an unvalued hedge at zero or a foreign position at par.
    4. A non-positive rate. Zero or negative is not an exchange rate; a zero
       spot is what an all-unconverted book implies (0 / net_ccy).

    A currency with no rate, no exposure on either leg and no hedge delta is not listed here —
    the rate was genuinely not required, nothing is misstated, and refusing a
    filed run over an absent display rate on an empty position would be a false
    refusal. The FX reader counts those separately for disclosure.
    """
    item = f"fact:fx_position:{currency}"
    context = {"currency": currency, "net_ccy": str(net_ccy), "net_ghs": str(net_ghs)}
    if net_ccy != _ZERO and net_ghs == _ZERO:
        return outcome(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id=_NOP_METRIC_ID,
            reason=(
                f"The {currency} book holds a net position of {net_ccy} {currency} but "
                "carries zero value in the reporting currency, so no exchange rate was "
                "applied to it. Zero would state that the position does not exist. "
                f"Ingest the reporting-currency balance or a current rate for {currency}."
            ),
            items=(item,),
            context=context,
        )
    if _ZERO not in (net_ccy, net_ghs) and (net_ccy > _ZERO) != (net_ghs > _ZERO):
        return outcome(
            OutcomeState.DATA_QUALITY_BLOCK,
            metric_id=_NOP_METRIC_ID,
            reason=(
                f"The {currency} net position is {'long' if net_ccy > _ZERO else 'short'} "
                f"in {currency} but {'long' if net_ghs > _ZERO else 'short'} in the "
                "reporting currency. No exchange rate produces that reversal, so part of "
                f"the {currency} book was converted and part of it was not. Reconcile the "
                f"reporting-currency balances on the {currency} positions."
            ),
            items=(item,),
            context=context,
        )
    if spot is None and (rate_required or net_ccy != _ZERO or net_ghs != _ZERO):
        return outcome(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id=_NOP_METRIC_ID,
            reason=(
                f"No exchange rate was established for {currency}, so its open position "
                "cannot be stated in the reporting "
                "currency. Counting it at par would misstate the net open position. "
                f"Ingest a current rate for {currency}."
            ),
            items=(item,),
            context=context,
        )
    if spot is not None and spot <= _ZERO:
        return outcome(
            OutcomeState.DATA_QUALITY_BLOCK,
            metric_id=_NOP_METRIC_ID,
            reason=(
                f"The {currency} revaluation rate resolved to {spot}, which is not an "
                "exchange rate. A zero rate is what a book implies when none of its "
                f"positions carry a reporting-currency balance. Ingest a current rate for "
                f"{currency} or the reporting-currency balances behind it."
            ),
            items=(item,),
            context={**context, "spot": str(spot)},
        )
    return None
