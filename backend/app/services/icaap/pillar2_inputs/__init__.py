"""Assembling a Pillar 2 method's inputs from what this cycle has bound.

A method never reaches into the engines. It reads the figures THIS cycle has
bound — the capital position, the Pillar 1 RWA, the IRRBB run, the FX position,
the attested Appendix II tables — so the number a method computes and the
number printed beside it in section (h) come from the same binding. When a
binding is missing the answer is a STATE (``not_computable``) with a plain
reason, not an exception: an ICAAP in progress is allowed to be incomplete.

Concentration is the exception that proves the rule. There is no sealed
concentration run, so the vectors are aggregated here from the canonical as-of
snapshot at full precision — never from the monitor's rounded top-N view, and
never from the live plane (D-034). A digest of those vectors is recorded as the
computation's canonical probe key, so a later ingestion that changes the book
makes the item stale.

The standardised framework adds one narrow second exception, and only where
there is nothing to bind. Its FIGURES always come from the bound block; but when
no block is bound, this module asks whether the framework was TRIED for this
date and refused, so the register can state the refusal under the engine's own
name (D-061) instead of reporting a missing link. "Nobody ran it" and "we ran it
and it refused" are different answers, and only the second tells a preparer what
to do.

Consolidated cycles cannot use bound blocks at all: every engine in the
platform produces solo figures (D-018). They supply the same inputs by hand
with an evidence attachment, and the item says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap.pillar2 import concentration as concentration_domain
from app.domain.icaap.pillar2 import fx as fx_domain
from app.domain.icaap.pillar2 import irrbb as irrbb_domain
from app.domain.icaap.pillar2 import irrbb_sf_method as irrbb_sf_domain
from app.domain.icaap.pillar2 import operational as operational_domain
from app.domain.icaap.pillar2 import sovereign as sovereign_domain
from app.domain.icaap.units import Denominators
from app.domain.stress.appendix_ii import thousands
from app.models.icaap import IcaapBlockBinding, IcaapCycle
from app.models.icaap_risk_capital import IcaapPillar2Item
from app.schemas.icaap_risk_capital import IcaapPillar2ManualInputs
from app.services import credit_concentration
from app.services.icaap import blocks as blocks_service
from app.services.icaap import digests, floors
from app.services.icaap.pillar2_inputs import granularity as granularity_inputs

#: The inverse of the Appendix II reporting scale. Derived from the scale
#: itself rather than written out, so the two can never disagree: the attested
#: tables publish thousands, and Pillar 2 amounts are in whole currency units.
FROM_REPORTING_SCALE = Decimal(1) / thousands(Decimal(1))

#: Where each figure comes from, so a missing block can be named in the reason.
BLOCK_PILLAR1 = "pillar1_rwa"
BLOCK_CAPITAL = "capital_position"
BLOCK_APPENDIX = "appendix_ii"
BLOCK_IRRBB = "irrbb"
#: The standardised framework's own block. A SIBLING of ``irrbb``: the interim
#: method reads the legacy engine's deltas, the framework method reads the
#: sealed ``irr_sf`` run, and neither ever reads the other's figures.
BLOCK_IRRBB_SF = "irrbb_sf"
BLOCK_FX = "fx_position"
BLOCK_SOVEREIGN = "sovereign_exposures"
BLOCK_FINANCIALS = "financials"
BLOCK_ILAAP = "ilaap"

_STRESS_YEAR_ONE = "stress_y1"


@dataclass
class MethodInputs:
    """Everything a method needs, plus what it was read from."""

    baseline: Denominators
    stressed: Denominators | None
    bindings: list[dict[str, Any]] = field(default_factory=list)
    missing_blocks: list[str] = field(default_factory=list)
    canonical_probe_key: str | None = None
    tier1: Decimal | None = None
    gross_income: Decimal | None = None
    #: The Pillar 1 RWA of the risk being quantified, for the methods that
    #: net their add-on against an existing charge (FX, operational).
    market_rwa: Decimal | None = None
    operational_rwa: Decimal | None = None
    stressed_operational_rwa: Decimal | None = None
    name_vector: concentration_domain.DimensionVector | None = None
    sector_vector: concentration_domain.DimensionVector | None = None
    fx_positions: tuple[fx_domain.CurrencyPosition, ...] = ()
    irrbb_deltas: tuple[irrbb_domain.ScenarioDelta, ...] = ()
    #: The sealed standardised framework figures, read off the bound block.
    sf_figures: irrbb_sf_domain.SfFigures | None = None
    #: The typed refusal the newest framework attempt carried, if it refused.
    #: Carried through so the register states a refusal rather than a gap.
    sf_refusal: str | None = None
    sovereign_holdings: tuple[sovereign_domain.SovereignHolding, ...] = ()
    operational_scenarios: tuple[operational_domain.OperationalScenario, ...] = ()
    ilaap_facts: dict[str, str | None] = field(default_factory=dict)
    #: The obligor book the FULL granularity adjustment measures. Absent for
    #: every other method — including the simplified concentration charge, which
    #: reads an index over the same book rather than the book itself.
    ga_book: granularity_inputs.GaBook | None = None
    manual: bool = False

    def note_missing(self, block_type: str) -> None:
        if block_type not in self.missing_blocks:
            self.missing_blocks.append(block_type)

    def digest_body(self) -> dict[str, Any]:
        """The value-based body a computation's ``inputs_digest`` covers."""
        return {
            "baseline": _denominators_body(self.baseline),
            "stressed": None if self.stressed is None else _denominators_body(self.stressed),
            "bindings": self.bindings,
            "canonical_probe_key": self.canonical_probe_key,
            "tier1": _text(self.tier1),
            "gross_income": _text(self.gross_income),
            "market_rwa": _text(self.market_rwa),
            "operational_rwa": _text(self.operational_rwa),
            "stressed_operational_rwa": _text(self.stressed_operational_rwa),
            "name_vector": _vector_body(self.name_vector),
            "sector_vector": _vector_body(self.sector_vector),
            "fx_positions": [
                {"currency": position.currency, "net": _text(position.net)}
                for position in self.fx_positions
            ],
            "irrbb_deltas": [
                {"code": delta.code, "delta_eve": _text(delta.delta_eve)}
                for delta in self.irrbb_deltas
            ],
            "sf_figures": _sf_figures_body(self.sf_figures),
            "sf_refusal": self.sf_refusal,
            "sovereign_holdings": [
                {
                    "key": holding.key,
                    "currency_kind": holding.currency_kind,
                    "tenor_bucket": holding.tenor_bucket,
                    "exposure": _text(holding.exposure),
                    "pillar1_rwa": _text(holding.pillar1_rwa),
                }
                for holding in self.sovereign_holdings
            ],
            "operational_scenarios": [
                {
                    "key": scenario.key,
                    "definition": scenario.definition,
                    "loss_amount": _text(scenario.loss_amount),
                }
                for scenario in self.operational_scenarios
            ],
            "ilaap_facts": self.ilaap_facts,
            "ga_book": _ga_book_body(self.ga_book),
            "manual": self.manual,
        }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _denominators_body(denominators: Denominators) -> dict[str, str | None]:
    return {
        "total_rwa": _text(denominators.total_rwa),
        "credit_rwa": _text(denominators.credit_rwa),
        "car_min_pct": _text(denominators.car_min_pct),
    }


def _vector_body(
    vector: concentration_domain.DimensionVector | None,
) -> dict[str, Any] | None:
    if vector is None:
        return None
    return {
        "dimension": vector.dimension,
        "stated": [str(value) for value in vector.stated],
        "unstated": str(vector.unstated),
    }


def _ga_book_body(book: granularity_inputs.GaBook | None) -> dict[str, Any] | None:
    """The obligor book, value-based, so a changed book changes the digest.

    Every exposure contributes — the adjustment is driven by each name's SHARE
    of the book, so two books with the same obligor count and the same total are
    different inputs — but they contribute through a digest of their own rather
    than as a list. A real book runs to tens of thousands of rows, and the item
    digest has to stay a fixed-size body. The nested digest is value-based and
    order-invariant exactly like the outer one: the rows are sorted by their own
    values and carry no snapshot ids.
    """
    if book is None:
        return None
    return {
        "exposures": digests.register_digest(
            {
                "rows": sorted(
                    ":".join(
                        (
                            exposure.ref,
                            exposure.group_key,
                            str(exposure.ead),
                            str(exposure.pd),
                            str(exposure.elgd),
                            exposure.segment,
                            str(exposure.maturity_years),
                            exposure.pd_source,
                            exposure.lgd_source,
                        )
                    )
                    for exposure in book.exposures
                )
            }
        ),
        "exposure_count": len(book.exposures),
        "excluded": dict(book.excluded),
        "refusals": list(book.refusals),
        "rows_read": book.rows_read,
    }


def _sf_figures(binding: IcaapBlockBinding) -> irrbb_sf_domain.SfFigures:
    """The framework figures, from the binding this cycle pinned.

    The numbers come from the block's FACTS and the markers and parameter codes
    from its payload's machine-readable half, so the figure the register
    capitalises and the table printed beside it in section (h) are the same
    binding — not two reads of an engine that may have run again since.
    """
    raw = (binding.payload or {}).get("raw", {}).get("irrbb_sf")
    body: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    tallies = body.get("assumption_tallies")
    return irrbb_sf_domain.SfFigures(
        eve_risk_measure=_decimal(blocks_service.fact_value(binding, "eve_risk_measure")),
        tier1=_decimal(blocks_service.fact_value(binding, "tier1")),
        pct_tier1=_decimal(blocks_service.fact_value(binding, "eve_risk_measure_pct_tier1")),
        outlier=_flag(blocks_service.fact_value(binding, "outlier")),
        worst_scenario=blocks_service.fact_value(binding, "worst_scenario"),
        max_delta_nii=_decimal(blocks_service.fact_value(binding, "delta_nii_max")),
        currencies_in_scope=blocks_service.fact_value(binding, "currencies_in_scope"),
        mandatory=_flag(blocks_service.fact_value(binding, "sf_mandatory")),
        run_id=_as_text(body.get("run_id")),
        run_input_hash=_as_text(body.get("input_hash")),
        measure_set=_as_text(body.get("measure_set")),
        assumption_tallies=(
            {str(marker): int(count) for marker, count in tallies.items()}
            if isinstance(tallies, Mapping)
            else {}
        ),
        representative_parameters=tuple(
            str(code) for code in body.get("representative_parameters") or []
        ),
        pending_parameters=tuple(
            str(code) for code in body.get("parameters_pending_confirmation") or []
        ),
        scenarios_in_measure=tuple(str(code) for code in body.get("scenarios_in_measure") or []),
    )


def _sf_refusal(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> str | None:
    from app.services.icaap import sf_state  # noqa: PLC0415 - avoid an import cycle

    refused = sf_state.refusal(db, access, cycle)
    return None if refused is None else refused[0]


def _flag(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"true", "yes", "1"}


def _sf_figures_body(figures: irrbb_sf_domain.SfFigures | None) -> dict[str, Any] | None:
    """The framework figures, value-based, so a new run changes the digest.

    The assumption tallies go in per marker rather than as a total: two books
    that applied the same NUMBER of defaults for different reasons are
    different inputs, and a digest that could not tell them apart would let one
    be substituted for the other without the item going stale.
    """
    if figures is None:
        return None
    return {
        "eve_risk_measure": _text(figures.eve_risk_measure),
        "tier1": _text(figures.tier1),
        "pct_tier1": _text(figures.pct_tier1),
        "outlier": figures.outlier,
        "worst_scenario": figures.worst_scenario,
        "max_delta_nii": _text(figures.max_delta_nii),
        "currencies_in_scope": figures.currencies_in_scope,
        "mandatory": figures.mandatory,
        "run_id": figures.run_id,
        "run_input_hash": figures.run_input_hash,
        "measure_set": figures.measure_set,
        "assumption_tallies": {
            str(marker): int(count) for marker, count in sorted(figures.assumption_tallies.items())
        },
        "representative_parameters": list(figures.representative_parameters),
        "pending_parameters": list(figures.pending_parameters),
        "scenarios_in_measure": list(figures.scenarios_in_measure),
    }


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _binding_ref(block_type: str, binding: IcaapBlockBinding) -> dict[str, Any]:
    return {
        "block_type": block_type,
        "block_id": str(binding.block_id),
        "seq": binding.seq,
        "source_key": binding.source_key,
        "payload_sha256": binding.payload_sha256,
    }


def _table_rows(binding: IcaapBlockBinding, table_key: str) -> list[dict[str, Any]]:
    payload = binding.payload or {}
    for table in payload.get("tables", []):
        if isinstance(table, dict) and table.get("key") == table_key:
            rows = table.get("rows")
            if isinstance(rows, list):
                return [row.get("cells", {}) for row in rows if isinstance(row, dict)]
    return []


def _stressed_denominators(
    binding: IcaapBlockBinding | None, car_min_pct: Decimal | None
) -> tuple[Denominators | None, Decimal | None]:
    """Year-one stressed RWA from the attested Appendix II Table 5.

    Returns the denominators a percentage basis is measured against, and the
    stressed OPERATIONAL RWA separately — the operational method nets its
    add-on against that risk's own Pillar 1 charge, which is not a denominator.

    The tables publish thousands; Pillar 2 amounts are whole currency units, so
    the figures are scaled back through the same constant the tables used.
    """
    if binding is None:
        return None, None
    raw = (binding.payload or {}).get("raw", {}).get("raw_appendix_ii")
    if not isinstance(raw, Mapping):
        return None, None
    table5 = raw.get("table5_rwa")
    if not isinstance(table5, Mapping):
        return None, None
    rows = table5.get("rows")
    if not isinstance(rows, list):
        return None, None
    for row in rows:
        if not isinstance(row, Mapping) or row.get("label") != _STRESS_YEAR_ONE:
            continue
        credit = _scale(row.get("credit_rwa"))
        total = _scale(row.get("total_pillar1_rwa"))
        return (
            Denominators(total_rwa=total, credit_rwa=credit, car_min_pct=car_min_pct),
            _scale(row.get("operational_rwa")),
        )
    return None, None


def _scale(value: Any) -> Decimal | None:
    """One published thousands figure back in whole currency units."""
    parsed = _decimal(_as_text(value))
    return None if parsed is None else parsed * FROM_REPORTING_SCALE


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _concentration_vectors(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[concentration_domain.DimensionVector, concentration_domain.DimensionVector, str]:
    """Full-precision vectors from the canonical as-of book, plus a probe key."""
    exposures = credit_concentration.load_credit_exposures(
        db, access.ctx, access.bank, cycle.as_of_date
    )
    name_vector = concentration_domain.aggregate_dimension(exposures, "single_name")
    sector_vector = concentration_domain.aggregate_dimension(exposures, "sector")
    probe = digests.register_digest(
        {
            "as_of": cycle.as_of_date.isoformat(),
            "exposure_count": len(exposures),
            "single_name": _vector_body(name_vector),
            "sector": _vector_body(sector_vector),
        }
    )
    return name_vector, sector_vector, probe


def _manual_vector(
    dimension: str, stated: Sequence[Decimal] | None, unstated: Decimal | None
) -> concentration_domain.DimensionVector | None:
    if stated is None:
        return None
    return concentration_domain.DimensionVector(
        dimension=dimension,
        stated=tuple(stated),
        unstated=unstated or Decimal(0),
    )


def build(  # noqa: PLR0912, PLR0915 - one branch per method's inputs, each named
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    item: IcaapPillar2Item,
    *,
    manual_inputs: IcaapPillar2ManualInputs | None = None,
) -> MethodInputs:
    """The inputs for ``item.method``, from this cycle's bindings or by hand."""
    car_floor = floors.car_min_pct(db, access, as_of=cycle.as_of_date)
    car_min_pct = car_floor.value_pct
    if item.input_mode == "manual_with_evidence":
        return _manual_build(item, manual_inputs, car_min_pct)

    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    inputs = MethodInputs(baseline=Denominators(car_min_pct=car_min_pct), stressed=None)

    pillar1 = bound.get(BLOCK_PILLAR1)
    capital = bound.get(BLOCK_CAPITAL)
    if pillar1 is not None:
        inputs.bindings.append(_binding_ref(BLOCK_PILLAR1, pillar1))
    if capital is not None:
        inputs.bindings.append(_binding_ref(BLOCK_CAPITAL, capital))
    total_rwa = _decimal(blocks_service.fact_value(pillar1, "total_rwa")) or _decimal(
        blocks_service.fact_value(capital, "total_rwa")
    )
    credit_rwa = _decimal(blocks_service.fact_value(pillar1, "credit_rwa"))
    market_rwa = _decimal(blocks_service.fact_value(pillar1, "market_rwa"))
    operational_rwa = _decimal(blocks_service.fact_value(pillar1, "operational_rwa"))
    inputs.baseline = Denominators(
        total_rwa=total_rwa, credit_rwa=credit_rwa, car_min_pct=car_min_pct
    )
    inputs.tier1 = _decimal(blocks_service.fact_value(bound.get(BLOCK_IRRBB), "tier1"))

    appendix = bound.get(BLOCK_APPENDIX)
    if appendix is not None:
        inputs.bindings.append(_binding_ref(BLOCK_APPENDIX, appendix))
    inputs.stressed, stressed_operational_rwa = _stressed_denominators(appendix, car_min_pct)

    method = item.method
    if method in {"benchmark_mapped", "hhi_proportional_heuristic", "granularity_adjustment"}:
        if pillar1 is None:
            inputs.note_missing(BLOCK_PILLAR1)
        name_vector, sector_vector, probe = _concentration_vectors(db, access, cycle)
        inputs.name_vector = name_vector
        inputs.sector_vector = sector_vector
        inputs.canonical_probe_key = probe
        if method == "granularity_adjustment":
            # The full adjustment needs every obligor's own PD, LGD and segment,
            # not the index the other two read, so it loads the book itself.
            inputs.ga_book = granularity_inputs.build_book(db, access, cycle)
    elif method == "irrbb_interim_delta_eve":
        irrbb = bound.get(BLOCK_IRRBB)
        if irrbb is None:
            inputs.note_missing(BLOCK_IRRBB)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_IRRBB, irrbb))
            inputs.irrbb_deltas = tuple(
                irrbb_domain.ScenarioDelta(code=str(cells.get("scenario_code")), delta_eve=delta)
                for cells in _table_rows(irrbb, "eve_by_scenario")
                if cells.get("scenario_code") is not None
                and (delta := _decimal(_as_text(cells.get("delta_eve")))) is not None
            )
        if capital is None and inputs.tier1 is None:
            inputs.note_missing(BLOCK_CAPITAL)
    elif method == irrbb_sf_domain.METHOD:
        sf_binding = bound.get(BLOCK_IRRBB_SF)
        if sf_binding is None:
            # Not a gap to fill silently: when the framework was TRIED for this
            # date and refused, the register says so under the engine's own
            # name (D-061) instead of reporting a missing link.
            inputs.sf_refusal = _sf_refusal(db, access, cycle)
            if inputs.sf_refusal is None:
                inputs.note_missing(BLOCK_IRRBB_SF)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_IRRBB_SF, sf_binding))
            inputs.sf_figures = _sf_figures(sf_binding)
            inputs.tier1 = inputs.sf_figures.tier1 or inputs.tier1
        if inputs.sf_refusal is None and capital is None and inputs.tier1 is None:
            # A refused measurement has no denominator problem to report: the
            # measure does not exist at all, and naming a second missing link
            # would bury the refusal the preparer has to act on.
            inputs.note_missing(BLOCK_CAPITAL)
    elif method == "fx_nop_addon":
        fx_binding = bound.get(BLOCK_FX)
        if fx_binding is None:
            inputs.note_missing(BLOCK_FX)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_FX, fx_binding))
            inputs.fx_positions = tuple(
                fx_domain.CurrencyPosition(currency=str(cells.get("currency")), net=net)
                for cells in _table_rows(fx_binding, "positions")
                if cells.get("currency") is not None
                and (net := _decimal(_as_text(cells.get("net_reporting")))) is not None
            )
        if pillar1 is None:
            inputs.note_missing(BLOCK_PILLAR1)
        inputs.market_rwa = market_rwa
    elif method == "sovereign_stress_addon":
        sovereign = bound.get(BLOCK_SOVEREIGN)
        if sovereign is None:
            inputs.note_missing(BLOCK_SOVEREIGN)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_SOVEREIGN, sovereign))
            inputs.sovereign_holdings = tuple(
                sovereign_domain.SovereignHolding(
                    key=str(cells.get("key") or cells.get("label") or ""),
                    currency_kind=str(cells.get("currency_kind") or ""),
                    tenor_bucket=(
                        None if cells.get("tenor_bucket") is None else str(cells["tenor_bucket"])
                    ),
                    exposure=exposure,
                    pillar1_rwa=_decimal(_as_text(cells.get("pillar1_rwa"))) or Decimal(0),
                )
                for cells in _table_rows(sovereign, "holdings")
                if (exposure := _decimal(_as_text(cells.get("exposure")))) is not None
            )
    elif method == "operational_scenario_net_p1":
        financials = bound.get(BLOCK_FINANCIALS)
        if financials is None:
            inputs.note_missing(BLOCK_FINANCIALS)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_FINANCIALS, financials))
            inputs.gross_income = _decimal(blocks_service.fact_value(financials, "gross_income"))
        if pillar1 is None:
            inputs.note_missing(BLOCK_PILLAR1)
        inputs.operational_rwa = operational_rwa
        inputs.stressed_operational_rwa = stressed_operational_rwa
        inputs.operational_scenarios = _scenarios_from(item)
    elif method == "not_capitalised":
        ilaap = bound.get(BLOCK_ILAAP)
        if ilaap is None:
            inputs.note_missing(BLOCK_ILAAP)
        else:
            inputs.bindings.append(_binding_ref(BLOCK_ILAAP, ilaap))
            inputs.ilaap_facts = {
                key: blocks_service.fact_value(ilaap, key)
                for key in ("ilaap_adequate", "cfp_approved", "lcr_pct", "nsfr_pct")
            }
    return inputs


def _scenarios_from(item: IcaapPillar2Item) -> tuple[operational_domain.OperationalScenario, ...]:
    definition = item.scenario_definition or {}
    raw = definition.get("scenarios")
    if not isinstance(raw, list):
        return ()
    return tuple(
        operational_domain.OperationalScenario(
            key=str(entry.get("key")),
            definition=None if entry.get("definition") is None else str(entry["definition"]),
            loss_amount=_decimal(_as_text(entry.get("loss_amount"))),
            evidence_attachment_id=(
                None
                if entry.get("evidence_attachment_id") is None
                else str(entry["evidence_attachment_id"])
            ),
        )
        for entry in raw
        if isinstance(entry, Mapping) and entry.get("key") is not None
    )


def _manual_build(
    item: IcaapPillar2Item,
    manual_inputs: IcaapPillar2ManualInputs | None,
    car_min_pct: Decimal | None,
) -> MethodInputs:
    supplied = manual_inputs or IcaapPillar2ManualInputs()
    inputs = MethodInputs(
        baseline=Denominators(
            total_rwa=supplied.total_rwa,
            credit_rwa=supplied.credit_rwa,
            car_min_pct=car_min_pct,
        ),
        stressed=(
            None
            if supplied.stressed_total_rwa is None and supplied.stressed_credit_rwa is None
            else Denominators(
                total_rwa=supplied.stressed_total_rwa,
                credit_rwa=supplied.stressed_credit_rwa,
                car_min_pct=car_min_pct,
            )
        ),
        manual=True,
    )
    inputs.tier1 = supplied.tier1
    inputs.gross_income = supplied.gross_income
    inputs.name_vector = _manual_vector(
        "single_name",
        supplied.concentration_single_name,
        supplied.concentration_single_name_unstated,
    )
    inputs.sector_vector = _manual_vector(
        "sector", supplied.concentration_sector, supplied.concentration_sector_unstated
    )
    inputs.market_rwa = supplied.market_rwa
    inputs.operational_rwa = supplied.operational_rwa
    inputs.stressed_operational_rwa = supplied.stressed_operational_rwa
    if supplied.fx_positions:
        inputs.fx_positions = tuple(
            fx_domain.CurrencyPosition(currency=currency, net=net)
            for currency, net in sorted(supplied.fx_positions.items())
        )
    if supplied.irrbb_deltas:
        inputs.irrbb_deltas = tuple(
            irrbb_domain.ScenarioDelta(code=code, delta_eve=delta)
            for code, delta in sorted(supplied.irrbb_deltas.items())
        )
    if supplied.sovereign_holdings:
        inputs.sovereign_holdings = tuple(
            sovereign_domain.SovereignHolding(
                key=str(entry.get("key", "")),
                currency_kind=str(entry.get("currency_kind", "")),
                tenor_bucket=(
                    None if entry.get("tenor_bucket") is None else str(entry["tenor_bucket"])
                ),
                exposure=Decimal(str(entry.get("exposure", 0))),
                pillar1_rwa=Decimal(str(entry.get("pillar1_rwa", 0))),
            )
            for entry in supplied.sovereign_holdings
        )
    if item.method == "operational_scenario_net_p1":
        inputs.operational_scenarios = _scenarios_from(item)
    return inputs


def evidence_ids(item: IcaapPillar2Item) -> list[UUID]:
    out: list[UUID] = []
    for raw in item.evidence_attachment_ids or []:
        try:
            out.append(UUID(str(raw)))
        except ValueError:  # pragma: no cover - the service validates on write
            continue
    return out


__all__ = [
    "BLOCK_APPENDIX",
    "BLOCK_CAPITAL",
    "BLOCK_FINANCIALS",
    "BLOCK_FX",
    "BLOCK_ILAAP",
    "BLOCK_IRRBB",
    "BLOCK_IRRBB_SF",
    "BLOCK_PILLAR1",
    "BLOCK_SOVEREIGN",
    "FROM_REPORTING_SCALE",
    "MethodInputs",
    "build",
    "evidence_ids",
]
