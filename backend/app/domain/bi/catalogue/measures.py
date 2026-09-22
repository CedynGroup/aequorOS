"""``portfolio`` measures: additive and ratio figures over the marts.

Every measure declares which FX rule it follows (D-015) by choosing its
column: balances and mix read ``balance_rc`` (derivation rule — an
unconverted foreign-currency position is NULL, excluded and counted; R2/R3
reconcile these to the balance-sheet facts), while classification figures —
NPL, PAR, provisions, coverage — read ``classification_exposure_rc``
(classification rule — the same position is ``0``, counted; R1 reconciles
these to the engine's NPL). Nothing here re-implements either rule; the
column already carries it.

Ratios are composed from other measures by id (``numerator`` /
``denominator``), so the compiler emits ``sum(num) / nullif(sum(den), 0)``
and a NULL denominator gives NULL. Band predicates are the SET of band codes
they cover, derived from the domain vocabulary — no threshold literal enters
the catalogue (pinned by ``tests/architecture/test_bi_catalogue_authority.py``).
"""

from __future__ import annotations

from app.domain.bi.catalogue.dimensions import (
    CREDIT,
    EVENT_DIMENSION_IDS,
    EVENT_TABLE,
    LIQUIDITY,
    POSITION_DIMENSION_IDS,
    POSITION_TABLE,
    RISK,
)
from app.domain.bi.catalogue.members import (
    Aggregation,
    ColumnRef,
    FavourableDirection,
    FxRule,
    MeasureDef,
    RowFilter,
    Sensitivity,
    TimeBehaviour,
    ValueType,
)
from app.domain.bi.extract import DEMAND_DEPOSIT_TYPES
from app.domain.credit.dpd_bands import DPD_BANDS

#: Authorization module → ``institution_types.default_modules`` entitlement slug
#: (BANK_MODULES / SDI_MODULES). ``risk`` is in both classes' sets; ``positions``
#: is bank-only and deliberately not used, so an SDI keeps its own book.
ENTITLEMENT_BY_MODULE: dict[str, str] = {
    CREDIT: "credit",
    LIQUIDITY: "liquidity",
    RISK: "risk",
}

LOANS = RowFilter("position_type", "in", ("LOAN",))
DEPOSITS = RowFilter("position_type", "in", ("DEPOSIT",))
NON_PERFORMING = RowFilter("non_performing", "is_true")
RESTRUCTURED = RowFilter("restructured", "is_true")
UNCONVERTED = RowFilter("fx_unconverted", "is_true")
ENCUMBERED = RowFilter("encumbered", "is_true")
DEMAND_DEPOSITS = RowFilter("deposit_account_type", "in", tuple(sorted(DEMAND_DEPOSIT_TYPES)))


def dpd_bands_from(minimum_days: int) -> tuple[str, ...]:
    """The band codes whose whole range is at or beyond ``minimum_days``.

    PAR-N is "exposure N or more days past due"; the analytical bands partition
    the day line at the PAR boundaries, so the predicate is a set of codes.
    """
    return tuple(band.code for band in DPD_BANDS if band.minimum >= minimum_days)


def _par_filters(minimum_days: int) -> tuple[RowFilter, ...]:
    return (LOANS, RowFilter("dpd_band", "in", dpd_bands_from(minimum_days)))


def _measure(  # noqa: PLR0913 - one keyword per declared measure attribute
    id: str,
    label: str,
    column: str,
    *,
    module: str,
    table: str = POSITION_TABLE,
    aggregation: Aggregation = "sum",
    time_behaviour: TimeBehaviour = "stock",
    sensitivity: Sensitivity = "aggregated",
    fx_rule: FxRule | None = None,
    value_type: ValueType = "amount",
    direction: FavourableDirection = "neutral",
    checks: tuple[str, ...] = (),
    filters: tuple[RowFilter, ...] = (),
    numerator: str | None = None,
    denominator: str | None = None,
    weight: str | None = None,
    over: str | None = None,
    description: str = "",
) -> MeasureDef:
    return MeasureDef(
        id=id,
        module=module,
        sensitivity=sensitivity,
        label=label,
        source=ColumnRef(table, column),
        description=description,
        measure_kind="portfolio",
        aggregation=aggregation,
        time_behaviour=time_behaviour,
        allowed_dimensions=EVENT_DIMENSION_IDS if table == EVENT_TABLE else POSITION_DIMENSION_IDS,
        grain="portfolio",
        entitlement=ENTITLEMENT_BY_MODULE[module],
        favourable_direction=direction,
        thresholds_source=None,
        reconciliation_checks=checks,
        engine_rule=None,
        fx_rule=fx_rule,
        advisory_designation=None,
        value_type=value_type,
        row_filters=filters,
        numerator=numerator,
        denominator=denominator,
        weight=weight,
        over=over,
    )


def _loan_measures() -> tuple[MeasureDef, ...]:
    return (
        _measure(
            "loans.balance_rc",
            "Gross loans",
            "balance_rc",
            fx_rule="derivation",
            checks=("R2",),
            filters=(LOANS,),
            description="Loan balances in the reporting currency; unconverted positions excluded.",
            module=CREDIT,
        ),
        _measure(
            "loans.classification_exposure_rc",
            "Classified loan exposure",
            "classification_exposure_rc",
            fx_rule="classification",
            checks=("R1",),
            filters=(LOANS,),
            description=(
                "The exposure the classification engine grades; unconverted loans count at zero."
            ),
            module=CREDIT,
        ),
        _measure(
            "loans.npl_exposure_rc",
            "Non-performing exposure",
            "classification_exposure_rc",
            fx_rule="classification",
            direction="lower_better",
            checks=("R1",),
            filters=(LOANS, NON_PERFORMING),
            module=CREDIT,
        ),
        _measure(
            "loans.npl_ratio_pct",
            "NPL ratio",
            "classification_exposure_rc",
            aggregation="ratio_of_sums",
            fx_rule="classification",
            value_type="pct",
            direction="lower_better",
            checks=("R1",),
            numerator="loans.npl_exposure_rc",
            denominator="loans.classification_exposure_rc",
            module=CREDIT,
        ),
        *(
            _measure(
                f"loans.par_{days}_exposure_rc",
                f"Exposure {days}+ days past due",
                "classification_exposure_rc",
                fx_rule="classification",
                direction="lower_better",
                filters=_par_filters(days),
                module=CREDIT,
            )
            for days in (30, 60, 90)
        ),
        *(
            _measure(
                f"loans.par_{days}_pct",
                f"Portfolio at risk, {days} days",
                "classification_exposure_rc",
                aggregation="ratio_of_sums",
                fx_rule="classification",
                value_type="pct",
                direction="lower_better",
                numerator=f"loans.par_{days}_exposure_rc",
                denominator="loans.classification_exposure_rc",
                module=CREDIT,
            )
            for days in (30, 60, 90)
        ),
        _measure(
            "loans.provision_required_rc",
            "Provisions required",
            "provision_required_rc",
            fx_rule="classification",
            checks=("R1",),
            filters=(LOANS,),
            module=CREDIT,
        ),
        _measure(
            "loans.provision_held_rc",
            "Provisions held",
            "provision_held_rc",
            fx_rule="classification",
            filters=(LOANS,),
            module=CREDIT,
        ),
        _measure(
            "loans.specific_provision_held_rc",
            "Provisions held against non-performing loans",
            "provision_held_rc",
            fx_rule="classification",
            filters=(LOANS, NON_PERFORMING),
            module=CREDIT,
        ),
        _measure(
            "loans.provision_coverage_pct",
            "Provision coverage",
            "provision_held_rc",
            aggregation="ratio_of_sums",
            fx_rule="classification",
            value_type="pct",
            direction="higher_better",
            checks=("R1",),
            numerator="loans.specific_provision_held_rc",
            denominator="loans.npl_exposure_rc",
            description="Provisions held on non-performing loans over non-performing exposure.",
            module=CREDIT,
        ),
        _measure(
            "loans.interest_in_suspense_rc",
            "Interest in suspense",
            "interest_in_suspense_rc",
            fx_rule="classification",
            filters=(LOANS,),
            module=CREDIT,
        ),
        _measure(
            "loans.collateral_rc",
            "Collateral value",
            "collateral_rc",
            fx_rule="classification",
            filters=(LOANS,),
            module=CREDIT,
        ),
        _measure(
            "loans.restructured_exposure_rc",
            "Restructured exposure",
            "classification_exposure_rc",
            fx_rule="classification",
            direction="lower_better",
            filters=(LOANS, RESTRUCTURED),
            module=CREDIT,
        ),
        _measure(
            "loans.count",
            "Number of loans",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            checks=("R5",),
            filters=(LOANS,),
            module=CREDIT,
        ),
        _measure(
            "loans.unconverted_count",
            "Loans without a reporting-currency conversion",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            direction="lower_better",
            checks=("R6",),
            filters=(LOANS, UNCONVERTED),
            module=CREDIT,
        ),
        _measure(
            "loans.weighted_average_rate",
            "Weighted average interest rate",
            "interest_rate",
            aggregation="weighted_avg",
            fx_rule="derivation",
            value_type="ratio",
            filters=(LOANS,),
            weight="loans.balance_rc",
            description="Balance-weighted contractual rate, as a fraction.",
            module=CREDIT,
        ),
        _measure(
            "loans.largest_single_name_share_pct",
            "Largest single-name share",
            "classification_exposure_rc",
            aggregation="top_n_share",
            sensitivity="restricted",
            fx_rule="classification",
            value_type="pct",
            direction="lower_better",
            filters=(LOANS,),
            over="counterparty.id",
            description="Share of classified exposure held by the largest obligor.",
            module=CREDIT,
        ),
        _measure(
            "loans.sector_hhi",
            "Sector concentration (HHI)",
            "classification_exposure_rc",
            aggregation="hhi",
            fx_rule="classification",
            value_type="ratio",
            direction="lower_better",
            filters=(LOANS,),
            over="loan.sector",
            module=CREDIT,
        ),
    )


def _deposit_measures() -> tuple[MeasureDef, ...]:
    return (
        _measure(
            "deposits.balance_rc",
            "Deposits",
            "balance_rc",
            fx_rule="derivation",
            checks=("R3",),
            filters=(DEPOSITS,),
            module=LIQUIDITY,
        ),
        _measure(
            "deposits.demand_balance_rc",
            "Demand deposits",
            "balance_rc",
            fx_rule="derivation",
            filters=(DEPOSITS, DEMAND_DEPOSITS),
            description="Current, call and savings balances — deemed demand-natured.",
            module=LIQUIDITY,
        ),
        _measure(
            "deposits.demand_share_pct",
            "Demand deposit share",
            "balance_rc",
            aggregation="share",
            fx_rule="derivation",
            value_type="pct",
            numerator="deposits.demand_balance_rc",
            denominator="deposits.balance_rc",
            module=LIQUIDITY,
        ),
        _measure(
            "deposits.count",
            "Number of deposit accounts",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            checks=("R5",),
            filters=(DEPOSITS,),
            module=LIQUIDITY,
        ),
        _measure(
            "deposits.unconverted_count",
            "Deposits without a reporting-currency conversion",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            direction="lower_better",
            checks=("R6",),
            filters=(DEPOSITS, UNCONVERTED),
            module=LIQUIDITY,
        ),
        _measure(
            "deposits.weighted_average_rate",
            "Weighted average deposit rate",
            "interest_rate",
            aggregation="weighted_avg",
            fx_rule="derivation",
            value_type="ratio",
            filters=(DEPOSITS,),
            weight="deposits.balance_rc",
            description="Balance-weighted contractual rate, as a fraction.",
            module=LIQUIDITY,
        ),
        _measure(
            "positions.encumbered_balance_rc",
            "Encumbered balance",
            "balance_rc",
            fx_rule="derivation",
            filters=(ENCUMBERED,),
            module=LIQUIDITY,
        ),
    )


def _position_measures() -> tuple[MeasureDef, ...]:
    return (
        _measure(
            "positions.balance_rc",
            "Balance",
            "balance_rc",
            fx_rule="derivation",
            checks=("R2", "R3"),
            description="Reporting-currency balance across every position type.",
            module=RISK,
        ),
        _measure(
            "positions.balance_native",
            "Balance in own currency",
            "balance_native",
            description="Meaningful only when sliced by currency.",
            module=RISK,
        ),
        _measure(
            "positions.notional_rc",
            "Notional",
            "notional_rc",
            fx_rule="derivation",
            module=RISK,
        ),
        _measure(
            "positions.count",
            "Number of positions",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            checks=("R5",),
            module=RISK,
        ),
        _measure(
            "positions.unconverted_count",
            "Positions without a reporting-currency conversion",
            "snapshot_id",
            aggregation="count",
            value_type="count",
            direction="lower_better",
            checks=("R6",),
            filters=(UNCONVERTED,),
            module=RISK,
        ),
        _measure(
            "positions.unconverted_share_pct",
            "Share of positions without a conversion",
            "snapshot_id",
            aggregation="share",
            value_type="pct",
            direction="lower_better",
            checks=("R6",),
            numerator="positions.unconverted_count",
            denominator="positions.count",
            module=RISK,
        ),
        _measure(
            "positions.weighted_average_rate",
            "Weighted average rate",
            "interest_rate",
            aggregation="weighted_avg",
            fx_rule="derivation",
            value_type="ratio",
            weight="positions.balance_rc",
            description="Balance-weighted contractual rate, as a fraction.",
            module=RISK,
        ),
    )


def _event_measures() -> tuple[MeasureDef, ...]:
    flows: list[MeasureDef] = [
        _measure(
            "events.amount_rc",
            "Loan movements",
            "amount_rc",
            table=EVENT_TABLE,
            aggregation="flow_sum",
            time_behaviour="flow",
            fx_rule="derivation",
            module=CREDIT,
        ),
        _measure(
            "events.count",
            "Number of loan events",
            "event_id",
            table=EVENT_TABLE,
            aggregation="count",
            time_behaviour="flow",
            value_type="count",
            module=CREDIT,
        ),
    ]
    typed_flows: tuple[tuple[str, str, FavourableDirection], ...] = (
        ("DISBURSEMENT", "Disbursements", "neutral"),
        ("REPAYMENT", "Repayments", "neutral"),
        ("WRITE_OFF", "Write-offs", "lower_better"),
        ("RECOVERY", "Recoveries", "higher_better"),
    )
    for event_type, label, direction in typed_flows:
        flows.append(
            _measure(
                f"events.{event_type.lower()}_rc",
                label,
                "amount_rc",
                table=EVENT_TABLE,
                aggregation="flow_sum",
                time_behaviour="flow",
                fx_rule="derivation",
                direction=direction,
                filters=(RowFilter("event_type", "in", (event_type,)),),
                module=CREDIT,
            )
        )
    flows.extend(
        (
            _measure(
                "events.unconverted_count",
                "Loan events without a reporting-currency conversion",
                "event_id",
                table=EVENT_TABLE,
                aggregation="count",
                time_behaviour="flow",
                value_type="count",
                direction="lower_better",
                checks=("R6",),
                filters=(RowFilter("fx_unconverted", "is_true"),),
                module=CREDIT,
            ),
            _measure(
                "events.unattributed_count",
                "Loan events not attributed to a facility snapshot",
                "event_id",
                table=EVENT_TABLE,
                aggregation="count",
                time_behaviour="flow",
                value_type="count",
                direction="lower_better",
                filters=(RowFilter("attribution_basis", "in", ("no_snapshot", "unmatched")),),
                description="Events whose branch and product could not be resolved (D-018).",
                module=CREDIT,
            ),
        )
    )
    return tuple(flows)


def portfolio_measures() -> tuple[MeasureDef, ...]:
    """Every ``portfolio`` measure, in catalogue order."""
    return (*_loan_measures(), *_deposit_measures(), *_position_measures(), *_event_measures())
