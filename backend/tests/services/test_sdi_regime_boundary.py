"""The SDI / universal-bank regime boundary (forensic audit 2026-08-21 sections 6, 10).

Three properties, each of which was violated at f33e869:

1. **A specialised deposit-taking institution is never shown a bank's forecast.**
   Every metric in the projection is registered under ``InstitutionClass.BANK``
   with a Basel methodology; there is no registered s.29 projection method, so
   the projection is refused rather than produced.
2. **The simplified s.29 capital ratio never invents a regulatory number.** The
   floor, every bucket weight, and the currency conversion of every exposure
   either come from real data or the ratio refuses.
3. **Neither class silently inherits the other's parameters.** An SDI whose own
   liquidity floors are unseeded gets a refusal, never the bank floor wearing an
   SDI label.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import OutcomeDetail, OutcomeState
from app.domain.authority.registry import REGISTRY, InstitutionClass, MetricFamily
from app.domain.capital.engine import CapitalFact, compute_rwa
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.liquidity_thresholds import LiquidityThresholdUpdate
from app.services import (
    enterprise_stress,
    liquidity_thresholds,
    pipeline,
    regulatory_capital,
    regulatory_forecasting,
    sdi_capital,
    sdi_capital_assurance,
    sdi_capital_checks,
    sdi_readiness,
    sdi_regime,
    sdi_views,
)
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)
_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
_SEED_START = date(2020, 1, 1)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _bank(db: Session, *, institution_type: str, currency: str = "GHS") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="RGM",
        currency=currency,
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _sdi(db: Session, **kwargs: str) -> Bank:
    return _bank(db, institution_type="savings_and_loans", **kwargs)


def _batch_lineage(db: Session, bank: Bank, ref: str) -> tuple[IngestionBatch, LineageRecord]:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=_AS_OF,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref=ref,
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    return batch, lineage


def _seed_capital(db: Session, bank: Bank, rows: list[tuple[str, str, str]]) -> None:
    """rows: (component, amount_ghs, tier)."""
    batch, lineage = _batch_lineage(db, bank, "regime-capital")
    for index, (component, amount, tier) in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=bank.id,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=_AS_OF,
                row_index=index,
                source_reference=f"CS/{index}",
                payload={"capital_component": component, "amount_ghs": amount, "tier": tier},
            )
        )
    db.flush()


def _seed_positions(
    db: Session, bank: Bank, rows: list[tuple[str, str, str, str, str | None]]
) -> None:
    """rows: (source_reference, position_type, currency, balance, balance_ghs|None)."""
    batch, lineage = _batch_lineage(db, bank, "regime-positions")
    common = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": _AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
    }
    for ref, ptype, currency, balance, balance_ghs in rows:
        position = CanonicalPosition(
            **common, source_reference=ref, position_type=ptype, currency=currency
        )
        db.add(position)
        db.flush()
        db.add(
            CanonicalPositionSnapshot(
                **common,
                validation_status="accepted",
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                attributes={} if balance_ghs is None else {"balance_ghs": balance_ghs},
            )
        )
    db.flush()


def _supersede(db: Session, param_code: str, scope_key: str = "sdi") -> None:
    """Close every seeded generation of ``param_code`` before ``_AS_OF``."""
    for row in db.scalars(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == param_code,
            RegulatoryParameter.scope_key == scope_key,
        )
    ):
        row.effective_to = date(2021, 1, 1)
    db.flush()


def _add_param(  # noqa: PLR0913 - a control-plane row is an explicit key + value
    db: Session,
    *,
    param_code: str,
    scope_key: str = "sdi",
    scope_type: str = "institution_class",
    value: Decimal | None = None,
    value_json: dict | None = None,
    unit: str = "percent",
) -> None:
    db.add(
        RegulatoryParameter(
            scope_type=scope_type,
            scope_key=scope_key,
            param_code=param_code,
            jurisdiction_code="GH",
            value_numeric=value,
            value_json=value_json,
            unit=unit,
            source_citation="test",
            confirmation_status="confirmed",
            effective_from=date(2025, 1, 1),
            status="approved",
            proposed_by="test",
            approved_by="test-checker",
        )
    )
    db.flush()


# --------------------------------------------------------------------------
# 1. Forecast: the Basel projection is bank-only
# --------------------------------------------------------------------------


def test_registry_declares_the_projection_bank_only() -> None:
    """The boundary this gate enforces is WS-A's declaration, not a second opinion."""
    bank_families = {e.metric_family for e in REGISTRY.for_institution_class(InstitutionClass.BANK)}
    sdi_families = {e.metric_family for e in REGISTRY.for_institution_class(InstitutionClass.SDI)}
    assert MetricFamily.FORECAST in bank_families
    assert MetricFamily.FORECAST not in sdi_families


def test_bank_forecast_gate_is_a_no_op_for_a_universal_bank(db_session: Session) -> None:
    bank = _bank(db_session, institution_type="universal_bank")
    assert sdi_regime.forecast_regime_applies(db_session, bank) is True
    # Returns without raising — every bank path is untouched.
    assert sdi_regime.require_bank_forecast_regime(db_session, bank) is None


def test_sdi_forecast_run_is_refused_before_anything_is_computed(db_session: Session) -> None:
    sdi = _sdi(db_session)
    assert sdi_regime.forecast_regime_applies(db_session, sdi) is False

    with pytest.raises(sdi_regime.RegimeNotApplicable) as exc:
        regulatory_forecasting.create_forecast_run(
            db_session,
            _CTX,
            sdi.id,
            ForecastRunCreate(reporting_period_id=uuid4(), scenario_code="base"),
        )

    assert exc.value.status_code == 409
    message = str(exc.value.detail)
    # Production copy: names what is withheld and why, no jargon or raw enums.
    assert "not supervised" in message
    assert "Act 930" in message
    # The regulator is resolved from the jurisdiction registry, not written in.
    assert "Bank of Ghana" in message
    assert "s29" not in message  # the regime CODE is never shown to a reader
    assert exc.value.state.value == "policy_unresolved"
    # No run row exists, so no Basel figure for an SDI can ever be read back.
    assert (
        db_session.scalars(select(RegulatoryRun).where(RegulatoryRun.bank_id == sdi.id)).all() == []
    )


def test_every_forecast_entry_point_is_gated_for_an_sdi(db_session: Session) -> None:
    sdi = _sdi(db_session)
    with pytest.raises(sdi_regime.RegimeNotApplicable):
        regulatory_forecasting.list_forecast_scenarios(db_session, _CTX, sdi.id)


def test_live_tier_leaves_the_projection_out_for_an_sdi(db_session: Session) -> None:
    sdi = _sdi(db_session)
    bank = _bank(db_session, institution_type="universal_bank")

    sdi_modules = {name for name, _ in pipeline._scoped_modules(db_session, sdi)}  # noqa: SLF001
    bank_modules = {name for name, _ in pipeline._scoped_modules(db_session, bank)}  # noqa: SLF001

    assert "forecast" not in sdi_modules
    # The bank's live tier is byte-identical: every module it had, it still has.
    assert "forecast" in bank_modules
    assert {"liquidity", "capital", "irr", "fx", "ftp", "rating"} <= bank_modules


# --------------------------------------------------------------------------
# 2. The s.29 ratio never invents a regulatory number
# --------------------------------------------------------------------------


def test_car_floor_with_no_value_refuses_instead_of_becoming_zero(db_session: Session) -> None:
    """A resolved-but-valueless floor used to read as 0%, making every ratio green."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "1000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _supersede(db_session, "car_min")
    _add_param(db_session, param_code="car_min", value_json={"note": "awaiting BoG"})

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert exc.value.status_code == 409
    assert "not a zero floor" in str(exc.value.detail)
    assert exc.value.details[0].items == ("param:car_min",)


def test_missing_risk_weight_refuses_instead_of_assuming_one_hundred(
    db_session: Session,
) -> None:
    """A weight that cannot be established used to become 100% silently, while the
    module docstring claimed nothing was hardcoded."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _supersede(db_session, "risk_weight_other_loans")

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert "risk_weight_other_loans" in str(exc.value.detail)
    assert exc.value.details[0].items == ("param:risk_weight_other_loans",)


def test_foreign_currency_exposure_without_a_conversion_is_excluded_not_taken_as_ghs(
    db_session: Session,
) -> None:
    """The USD loan's balance is 1,000,000 USD. Counting it as 1,000,000 GHS (the
    old behaviour) understated it; converting it at an invented rate would be
    worse. It is left out and reported, exactly as the liquidity ladder does."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(
        db_session,
        sdi,
        [
            ("LN/GHS", "LOAN", "GHS", "100000000", "100000000"),
            ("LN/USD", "LOAN", "USD", "1000000", None),
            # A converted foreign-currency position still counts, at its
            # ingested conversion.
            ("LN/EUR", "LOAN", "EUR", "500000", "9000000"),
        ],
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    loans = next(band for band in summary.bands if band.bucket == "other_loans")
    assert loans.exposure_ghs == Decimal("109000000")  # 100m + 9m; the USD loan is out
    assert summary.total_rwa_ghs == Decimal("109000000")
    assert summary.unconverted_position_count == 1
    assert summary.unconverted_currencies == ("USD",)


def test_unconverted_exposure_blocks_filing_and_marks_the_ratio_provisional(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(
        db_session,
        sdi,
        [
            ("LN/GHS", "LOAN", "GHS", "100000000", "100000000"),
            ("LN/USD", "LOAN", "USD", "1000000", None),
        ],
    )

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)

    assert assurance.current.assessment_status == "provisional"
    assert any("no converted balance" in blocker for blocker in assurance.filing_blockers)
    assert any("overstated" in blocker for blocker in assurance.filing_blockers)


def test_bank_reporting_currency_positions_still_use_the_native_balance(
    db_session: Session,
) -> None:
    """A position already in the institution's own currency needs no conversion —
    the native balance is used, as before."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "40000000", None)])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert summary.total_rwa_ghs == Decimal("40000000")
    assert summary.unconverted_position_count == 0


def test_a_non_ghs_institution_uses_its_own_reporting_currency(db_session: Session) -> None:
    """The conversion rule is keyed on the institution's currency, not on GHS."""
    sdi = _bank(db_session, institution_type="finance_house", currency="NGN")
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "NGN", "40000000", None)])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert summary.total_rwa_ghs == Decimal("40000000")
    assert summary.unconverted_position_count == 0


# --------------------------------------------------------------------------
# 2b. The bucket taxonomy is governed policy data
# --------------------------------------------------------------------------


def test_bucket_taxonomy_defaults_are_flagged_and_block_filing(db_session: Session) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert summary.bucket_map_source == sdi_capital.BUCKET_MAP_CODE_DEFAULT
    assert summary.taxonomy_confirmed is False

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)
    assert any("product type to risk-weight band" in b for b in assurance.filing_blockers)


def test_a_governed_bucket_map_overrides_the_code_default(db_session: Session) -> None:
    """The seam is real: the control plane, not the code, decides which band a
    product falls in — including the 50% mortgage band, which the code default
    cannot reach."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _add_param(
        db_session,
        param_code=sdi_capital.BUCKET_MAP_PARAM,
        value_json={"LOAN": "mortgage"},
        unit="count",
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert summary.bucket_map_source == sdi_capital.BUCKET_MAP_CONTROL_PLANE
    assert summary.taxonomy_confirmed is True
    band = next(b for b in summary.bands if b.bucket == "mortgage")
    assert band.weight_pct == Decimal("50")
    assert summary.total_rwa_ghs == Decimal("50000000")


# --------------------------------------------------------------------------
# 2c. Capital-structure amounts are signed in BOTH readers
# --------------------------------------------------------------------------


def test_a_deduction_row_subtracts_from_the_component_it_names(db_session: Session) -> None:
    """``capital_components`` used ``abs()``, so a deduction row INCREASED the
    paid-up total the licensing floor is checked against — while the very same
    rows were summed signed one module away."""
    sdi = _sdi(db_session)
    _seed_capital(
        db_session,
        sdi,
        [
            ("paid_up_capital", "20000000", "CET1"),
            ("paid_up_capital", "5000000", "CET1_DEDUCTION"),
            ("statutory_reserves", "8000000", "CET1"),
            ("statutory_reserves", "-2000000", "CET1"),
        ],
    )
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])

    components = sdi_capital_checks.capital_components(db_session, _CTX, sdi, _AS_OF)
    assert components["paid_up_capital"] == Decimal("15000000")  # 20m - 5m, not 25m
    assert components["statutory_reserves"] == Decimal("6000000")  # 8m - 2m, not 10m

    # And the two readers now agree: the signed component total IS Net Own Funds.
    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert sum(components.values(), Decimal("0")) == summary.net_own_funds_ghs

    check = sdi_capital_checks.check_paid_up_capital(db_session, _CTX, sdi, _AS_OF)
    assert check.actual_ghs == Decimal("15000000")


# --------------------------------------------------------------------------
# 3. Neither class inherits the other's parameters
# --------------------------------------------------------------------------


def test_sdi_register_refuses_rather_than_showing_the_bank_floor(db_session: Session) -> None:
    sdi = _sdi(db_session)
    db_session.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "narrow_to_volatile",
            RegulatoryParameter.scope_key == "sdi",
        )
    )
    db_session.flush()

    with pytest.raises(Exception) as exc:  # noqa: B017 - HTTPException, asserted below
        liquidity_thresholds.get_register(db_session, _CTX, sdi.id, _AS_OF)

    assert getattr(exc.value, "status_code", None) == 409
    detail = str(getattr(exc.value, "detail", ""))
    assert "narrow_to_volatile" in detail
    # The bank floor is never offered as the answer.
    assert "80" not in detail


def test_bank_register_still_falls_back_to_the_published_bank_floor(
    db_session: Session,
) -> None:
    """The bank path is unchanged, including with an unseeded control plane."""
    bank = _bank(db_session, institution_type="universal_bank")
    db_session.execute(delete(RegulatoryParameter).where(RegulatoryParameter.scope_key == "bank"))
    db_session.flush()

    register = liquidity_thresholds.get_register(db_session, _CTX, bank.id, _AS_OF)
    by_code = {row.threshold_code: row for row in register.thresholds}
    for code, floor in liquidity_thresholds.BANK_MINIMUM_PCT.items():
        assert by_code[code].threshold_pct == floor, code
        assert by_code[code].institution_class == "bank", code


def test_seeded_sdi_and_bank_floors_never_coincide_by_accident(db_session: Session) -> None:
    """Both registers resolve from the control plane, and the SDI values are the
    SDI values (LMTD 2026 para 9), not the bank ones."""
    sdi = _sdi(db_session)
    bank = _bank(db_session, institution_type="universal_bank")
    sdi_register = liquidity_thresholds.get_register(db_session, _CTX, sdi.id, _AS_OF)
    bank_register = liquidity_thresholds.get_register(db_session, _CTX, bank.id, _AS_OF)
    sdi_floors = {row.threshold_code: row.threshold_pct for row in sdi_register.thresholds}
    bank_floors = {row.threshold_code: row.threshold_pct for row in bank_register.thresholds}

    assert sdi_floors["narrow_to_volatile"] == Decimal("90")
    assert bank_floors["narrow_to_volatile"] == Decimal("80")
    assert sdi_floors["broad_to_total_assets"] == Decimal("40")
    assert bank_floors["broad_to_total_assets"] == Decimal("50")


def test_a_board_generation_for_the_other_class_is_refused(db_session: Session) -> None:
    """The register is read back for the tenant's own class, so a generation
    recorded under the other class would never be applied — the payload field's
    "bank" default made that the silent outcome for an SDI."""
    sdi = _sdi(db_session)
    with pytest.raises(Exception) as exc:  # noqa: B017 - HTTPException, asserted below
        liquidity_thresholds.update_register(
            db_session,
            _CTX,
            sdi.id,
            LiquidityThresholdUpdate(
                institution_class="bank",
                effective_from=date(2026, 4, 1),
                approved_by="Board minute 1",
                thresholds={"narrow_to_volatile": Decimal("95")},
                reason="Annual review.",
            ),
        )
    assert getattr(exc.value, "status_code", None) == 422
    assert "supervised as 'sdi'" in str(getattr(exc.value, "detail", ""))


def test_a_board_generation_for_its_own_class_is_recorded_and_read_back(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    register = liquidity_thresholds.update_register(
        db_session,
        _CTX,
        sdi.id,
        LiquidityThresholdUpdate(
            institution_class="sdi",
            effective_from=date(2026, 4, 1),
            approved_by="Board minute 1",
            thresholds={"narrow_to_volatile": Decimal("95")},
            reason="Annual review adopted a stricter internal floor.",
        ),
    )
    by_code = {row.threshold_code: row for row in register.thresholds}
    assert by_code["narrow_to_volatile"].threshold_pct == Decimal("95")
    assert by_code["narrow_to_volatile"].source == "board_register"
    # Tighten-only still holds: the SDI regulatory floor is 90, the board's 95 stands.
    assert by_code["broad_to_short_term"].threshold_pct == Decimal("60")


def test_readiness_reports_the_unconverted_exposure_the_ratio_leaves_out(
    db_session: Session,
) -> None:
    """The same positions the ratio excludes are the ones onboarding is told to
    fix — reported as production copy on the readiness view, not a log line."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(
        db_session,
        sdi,
        [
            ("LN/GHS", "LOAN", "GHS", "100000000", "100000000"),
            ("LN/USD", "LOAN", "USD", "1000000", None),
        ],
    )

    modules = {
        m.module: m for m in sdi_readiness.assess_sdi_readiness(db_session, _CTX, sdi, _AS_OF)
    }
    capital = modules["capital"]
    assert capital.status == "partial"
    assert any("USD" in reason and "converted balance" in reason for reason in capital.reasons)


def test_an_empty_band_with_no_weight_does_not_block_the_ratio(db_session: Session) -> None:
    """Refusing is about protecting the number, not ceremony: a band holding no
    exposure cannot change risk-weighted assets whatever its weight."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(
        db_session,
        sdi,
        [
            ("LN/1", "LOAN", "GHS", "100000000", "100000000"),
            ("OTH/0", "OTHER_ASSET", "GHS", "0", "0"),
        ],
    )
    _supersede(db_session, "risk_weight_other_assets")

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert summary.total_rwa_ghs == Decimal("100000000")
    assert [band.bucket for band in summary.bands] == ["other_loans"]


# --------------------------------------------------------------------------
# 4. An unconfirmed regulatory figure is never presented as settled
# --------------------------------------------------------------------------


def test_an_unconfirmed_capital_floor_makes_the_ratio_provisional(
    db_session: Session,
) -> None:
    """WS-K convicted several SDI seeds as citing instruments made under a
    repealed law. Nothing here may assume a figure is settled — the control
    plane's own confirmation status decides, so re-statusing a parameter is all
    it takes to flow through."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    for row in db_session.scalars(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "car_min",
            RegulatoryParameter.scope_key == "sdi",
        )
    ):
        row.confirmation_status = "pending"
    db_session.flush()

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    assert summary.car_min_confirmation == "pending"

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)
    assert assurance.current.assessment_status == "provisional"
    assert any(
        "not yet confirmed against a published regulatory instrument" in blocker
        and "capital adequacy ratio" in blocker
        for blocker in assurance.filing_blockers
    )


def test_an_unconfirmed_exposure_limit_is_flagged_on_the_exposure_view(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    for row in db_session.scalars(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "large_exposure_limit_pct",
            RegulatoryParameter.scope_key == "sdi",
        )
    ):
        row.confirmation_status = "pending"
    db_session.flush()

    report = sdi_views.get_sdi_large_exposures(db_session, _CTX, sdi, _AS_OF)
    assert any(
        "large_exposure_limit_pct" in finding and "provisional" in finding
        for finding in report.findings
    )


def test_confirmed_limits_add_no_provisional_note(db_session: Session) -> None:
    """The note appears only when a limit is actually unconfirmed — it is not
    boilerplate on every response."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])

    report = sdi_views.get_sdi_large_exposures(db_session, _CTX, sdi, _AS_OF)
    assert not any("provisional" in finding for finding in report.findings)


# --------------------------------------------------------------------------
# 2c. The RWA composition is governed policy data, and its scope is disclosed
# --------------------------------------------------------------------------
#
# Forensic audit 2026-08-21, "DIVERGENCE #1": the s.29 ratio charged for credit
# risk alone and nothing on any output surface said so. Which risk classes it
# covers is now declared data, and every class it leaves out is reported.


def test_the_default_composition_is_credit_only_and_says_so(db_session: Session) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert summary.composition_source == sdi_capital.COMPOSITION_CODE_DEFAULT
    assert summary.composition_confirmed is False
    assert summary.included_risk_classes == ("credit",)
    assert summary.excluded_risk_classes == ("market", "operational")
    # Every known class is reported, in scope or not — the omission is visible.
    assert {row.risk_class for row in summary.risk_classes} == set(
        sdi_capital.KNOWN_RISK_CLASSES
    )
    # Production copy on the surface that presents the ratio.
    assert "credit risk only" in summary.rwa_scope_note
    assert "No market and operational risk charge is applied" in summary.rwa_scope_note
    # The reason each omitted class contributes nothing names the regulator from
    # the jurisdiction registry, never a country literal in code.
    market = next(row for row in summary.risk_classes if row.risk_class == "market")
    assert market.in_scope is False
    assert market.rwa_ghs == Decimal("0")
    assert "Bank of Ghana" in market.note
    assert "no charge is assumed" in market.note


def test_an_unconfirmed_composition_is_provisional_and_blocks_filing(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])

    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)

    assert assurance.current.assessment_status == "provisional"
    assert any("credit risk only" in blocker for blocker in assurance.filing_blockers)
    assert any(
        "not a scope approved for this institution" in blocker
        for blocker in assurance.filing_blockers
    )


def test_a_governed_composition_turns_a_prescribed_charge_on_without_a_code_change(
    db_session: Session,
) -> None:
    """The seam: two control-plane rows bring operational risk into the total."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _add_param(
        db_session,
        param_code=sdi_capital.COMPOSITION_PARAM,
        value_json={
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "operational": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
        },
        unit="count",
    )
    _add_param(
        db_session,
        param_code=sdi_capital.charge_param_code("operational"),
        value=Decimal("12"),
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert summary.composition_source == sdi_capital.COMPOSITION_CONTROL_PLANE
    assert summary.composition_confirmed is True
    assert summary.included_risk_classes == ("credit", "operational")
    assert summary.excluded_risk_classes == ("market",)
    # 100m credit RWA + 12% of it = 112m.
    assert summary.total_rwa_ghs == Decimal("112000000")
    operational = next(row for row in summary.risk_classes if row.risk_class == "operational")
    assert operational.rwa_ghs == Decimal("12000000")
    assert "12% of credit risk-weighted assets" in operational.note
    # An approved scope is a decision, not an omission: it no longer blocks filing.
    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)
    assert not any(
        "not a scope approved for this institution" in blocker
        for blocker in assurance.filing_blockers
    )
    # ...and the exclusion of market risk is still stated on the ratio itself.
    market = next(row for row in summary.risk_classes if row.risk_class == "market")
    assert "does not include market risk" in market.note


def test_a_declared_class_with_no_charge_refuses_instead_of_contributing_zero(
    db_session: Session,
) -> None:
    """The whole point of the composition: a class that is IN scope and silently
    worth nothing is exactly the defect being closed."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _add_param(
        db_session,
        param_code=sdi_capital.COMPOSITION_PARAM,
        value_json={
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "market": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
        },
        unit="count",
    )
    # No rwa_charge_market_pct_of_credit_rwa row exists.

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert exc.value.status_code == 409
    assert "rwa_charge_market_pct_of_credit_rwa" in str(exc.value.detail)
    assert "none is borrowed from the bank framework" in str(exc.value.detail)


def test_an_unrecognised_measurement_refuses_rather_than_scoring_zero(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _add_param(
        db_session,
        param_code=sdi_capital.COMPOSITION_PARAM,
        value_json={"credit": "basel_standardised"},
        unit="count",
    )

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert "basel_standardised" in str(exc.value.detail)
    assert "not a zero component" in str(exc.value.detail)


def test_a_class_put_explicitly_out_of_scope_contributes_nothing_and_is_reported(
    db_session: Session,
) -> None:
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", "100000000", "100000000")])
    _add_param(
        db_session,
        param_code=sdi_capital.COMPOSITION_PARAM,
        value_json={
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "market": False,
            "operational": None,
        },
        unit="count",
    )

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert summary.composition_source == sdi_capital.COMPOSITION_CONTROL_PLANE
    assert summary.total_rwa_ghs == Decimal("100000000")
    assert summary.excluded_risk_classes == ("market", "operational")


# --------------------------------------------------------------------------
# 2d. ONE authority: the live s.29 view and the official filing run cannot
#     charge for different risk classes
# --------------------------------------------------------------------------
#
# Making the composition governed data (§2c) fixed the live view and left the
# OFFICIAL path — ``regulatory_capital._SDI_STRUCTURAL_CAPITAL`` — restating the
# same scope structurally, by zeroing ``fx_charge_pct`` and ``bia_alpha_pct``.
# Two authorities for one question: a governed row that turned a charge on would
# have moved the live CAR and left the immutable filing CAR behind, which is
# exactly the "multiple uncontrolled authorities for one material metric" defect
# the whole programme exists to remove. These tests fail if the paths re-fork.

_MILLION = Decimal("1000000")
#: 100m of loans in the canonical book == 100m of RW100 loan-exposure facts, so
#: BOTH paths measure the same credit RWA and their totals are directly
#: comparable. Any divergence in what is charged ON TOP is then the only thing a
#: difference between them can mean.
_BOOK_GHS = "100000000"


def _official_facts() -> tuple[CapitalFact, ...]:
    """The filing run's view of the same book, plus an open FX position and a
    gross-income series — the two bases the Basel measurements would use. Neither
    may leak into an s.29 total: under s.29 a charged class is a percentage of
    credit RWA, never an FX open position or a BIA average."""
    return (
        CapitalFact(
            fact_group="loan_exposure",
            category="other_loans",
            amount=Decimal(_BOOK_GHS),
            risk_weight_code="RW100",
        ),
        CapitalFact(
            fact_group="market_risk", category="net_long_fx", amount=Decimal("40") * _MILLION
        ),
        *(
            CapitalFact(
                fact_group="operational_income",
                category=f"gross_income_{year}",
                amount=Decimal("60") * _MILLION,
                income_year=year,
            )
            for year in (2023, 2024, 2025)
        ),
    )


def _official_rwa(db: Session, sdi: Bank) -> tuple[Decimal, Decimal, dict[str, Decimal]]:
    """Risk-weighted assets exactly as the official run computes them: the active
    params the run loads, the shared engine, the same facts."""
    active = regulatory_capital._load_active_params(  # pyright: ignore[reportPrivateUsage]
        db, _CTX, sdi, _AS_OF
    )
    assert active.institution_class == "sdi"
    params = regulatory_capital._engine_params(  # pyright: ignore[reportPrivateUsage]
        replace(active, risk_weights={"RW0": Decimal("0"), "RW100": Decimal("100")})
    )
    result = compute_rwa(_official_facts(), params)
    return result.credit_rwa, result.total_rwa, dict(params.rwa_pct_of_credit_rwa)


def _governed_scope(db: Session, sdi: Bank, composition: dict[str, object] | None) -> None:
    if composition is None:
        return
    _add_param(
        db, param_code=sdi_capital.COMPOSITION_PARAM, value_json=composition, unit="count"
    )


@pytest.mark.parametrize(
    ("composition", "charges", "expected_total"),
    [
        pytest.param(None, {}, "100000000", id="code_default_credit_only"),
        pytest.param(
            {"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE},
            {},
            "100000000",
            id="governed_credit_only",
        ),
        pytest.param(
            {
                "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
                "operational": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
            },
            {"operational": Decimal("12")},
            "112000000",
            id="operational_charge_on",
        ),
        pytest.param(
            {
                "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
                "market": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
            },
            {"market": Decimal("5")},
            "105000000",
            id="market_charge_on",
        ),
        pytest.param(
            {
                "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
                "market": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
                "operational": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
            },
            {"market": Decimal("5"), "operational": Decimal("12")},
            "117000000",
            id="both_charges_on",
        ),
    ],
)
def test_the_live_view_and_the_filing_run_charge_for_the_same_risk_classes(
    db_session: Session,
    composition: dict[str, object] | None,
    charges: dict[str, Decimal],
    expected_total: str,
) -> None:
    """The anti-fork property. Same institution, same book, same governed scope —
    the two paths must produce the same risk-weighted assets, for every scope the
    control plane can express. Verified negatively: with the official path reverted
    to a restated scope, the three cases that charge something fail — the live total
    moves and the official total stays at 100m."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])
    _governed_scope(db_session, sdi, composition)
    for risk_class, pct in charges.items():
        _add_param(db_session, param_code=sdi_capital.charge_param_code(risk_class), value=pct)

    scope = sdi_capital.resolve_rwa_scope(db_session, sdi, _AS_OF)
    live = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)
    credit_rwa, total_rwa, engine_charges = _official_rwa(db_session, sdi)

    # Same book on both sides, so the totals are comparable without a ratio.
    assert credit_rwa == Decimal(_BOOK_GHS)
    assert live.total_rwa_ghs == Decimal(expected_total)
    assert total_rwa == Decimal(expected_total)
    assert total_rwa == live.total_rwa_ghs
    # ...and both are the closed form of the ONE governed scope, so neither can
    # drift without the scope drifting.
    assert scope.total_rwa_from_credit(credit_rwa) == total_rwa
    assert scope.total_rwa_from_credit(credit_rwa) == live.total_rwa_ghs
    # The official path carries the governed charges rather than restating a scope.
    assert engine_charges == charges
    assert dict(scope.pct_of_credit_rwa) == charges
    assert set(live.included_risk_classes) == {"credit", *charges}


def test_the_filing_run_never_measures_an_sdi_charge_the_basel_way(
    db_session: Session,
) -> None:
    """A charged class is a percentage of CREDIT RWA. The 40m open FX position and
    the 60m gross-income series in the fixture are the Basel bases; if either ever
    reached an s.29 total, the numbers below would not be exact multiples of it."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])
    _governed_scope(
        db_session,
        sdi,
        {
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "market": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
            "operational": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
        },
    )
    _add_param(db_session, param_code=sdi_capital.charge_param_code("market"), value=Decimal("5"))
    _add_param(
        db_session, param_code=sdi_capital.charge_param_code("operational"), value=Decimal("12")
    )

    active = regulatory_capital._load_active_params(  # pyright: ignore[reportPrivateUsage]
        db_session, _CTX, sdi, _AS_OF
    )
    params = regulatory_capital._engine_params(  # pyright: ignore[reportPrivateUsage]
        replace(active, risk_weights={"RW0": Decimal("0"), "RW100": Decimal("100")})
    )
    result = compute_rwa(_official_facts(), params)

    assert result.market_rwa == Decimal(_BOOK_GHS) * Decimal("5") / Decimal("100")
    assert result.operational_rwa == Decimal(_BOOK_GHS) * Decimal("12") / Decimal("100")
    # The Basel measurement rates stay off: no FX capital charge, no BIA charge.
    assert params.fx_charge_pct == Decimal("0")
    assert params.bia_alpha_pct == Decimal("0")
    assert result.fx_charge == Decimal("0")
    assert result.bia_charge == Decimal("0")


def test_a_charge_that_does_not_resolve_refuses_the_filing_run_too(
    db_session: Session,
) -> None:
    """The live view already refused a declared class with no charge. The filing
    run must refuse on the same governed row — before the run is created, so no
    immutable run is ever minted against a total missing a declared component."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])
    _governed_scope(
        db_session,
        sdi,
        {
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "market": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
        },
    )
    # No rwa_charge_market_pct_of_credit_rwa row exists.

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        regulatory_capital._load_active_params(  # pyright: ignore[reportPrivateUsage]
            db_session, _CTX, sdi, _AS_OF
        )

    assert exc.value.status_code == 409
    assert "rwa_charge_market_pct_of_credit_rwa" in str(exc.value.detail)


def test_the_solvency_stress_path_charges_the_same_classes(db_session: Session) -> None:
    """The third consumer. ``enterprise_stress`` built its own SDI capital params
    from the same structural dict; it now reads the governed scope, so a stress
    path can never charge for a different set than the position it stresses."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])
    _governed_scope(
        db_session,
        sdi,
        {
            "credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE,
            "operational": sdi_capital.MEASURE_PCT_OF_CREDIT_RWA,
        },
    )
    _add_param(
        db_session, param_code=sdi_capital.charge_param_code("operational"), value=Decimal("12")
    )

    params = enterprise_stress._sdi_capital_params(  # pyright: ignore[reportPrivateUsage]
        db_session,
        sdi,
        _AS_OF,
        {"car_min": Decimal("10")},
        {"RW0": Decimal("0"), "RW100": Decimal("100")},
        {},
    )

    assert dict(params.rwa_pct_of_credit_rwa) == {"operational": Decimal("12")}


# --------------------------------------------------------------------------
# 2e. The scope is a REGULATORY DETERMINATION, so an official run refuses
#     without one (independent forensic re-audit 2026-08-22, D-19)
# --------------------------------------------------------------------------
#
# The prior gate on the official path was ``if not scope.credit_in_scope``, and
# the code default IS credit-only, so it was always True: an SDI with no
# ``sdi_rwa_composition`` row minted a sealed, immutable CAR against the
# platform's own placeholder, and the run recorded no ``composition_source``.
#
# What Bank of Ghana actually says (verified against the primary instruments,
# see ``docs/bog_parameter_sources.md`` §2.4): NOTHING. The Capital Requirements
# Directive 2018 ¶2 confines itself to "banks licensed and operating under the
# BSDI Act", and it is the only instrument defining risk-weighted assets as
# "credit risk + market risk + operational risk" (¶73(a)). Act 930 s.29(2) fixes
# a ≥10% floor but s.29(4)-(5) delegate the methodology and the "categories of
# risk assets" to a directive that, for savings and loans companies and finance
# houses, does not exist. So the answer is not "credit only" — it is undetermined,
# and the platform must not resolve it by defaulting.


def test_an_official_capital_run_refuses_an_undetermined_rwa_scope(
    db_session: Session,
) -> None:
    """No governed row: the mint refuses rather than sealing a CAR."""
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])

    scope = sdi_capital.resolve_rwa_scope(db_session, sdi, _AS_OF)
    assert scope.source == sdi_capital.COMPOSITION_CODE_DEFAULT
    # The pre-fix gate would have passed: the code default IS credit-only.
    assert scope.credit_in_scope is True
    assert scope.filable is False

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.assert_official_rwa_scope_governed(db_session, sdi, _AS_OF)

    assert exc.value.status_code == 409
    # Doubly typed: a 409 for the API caller AND a WS-A outcome for a boundary
    # that already handles fail-closed outcomes.
    assert exc.value.state is OutcomeState.POLICY_UNRESOLVED
    detail = exc.value.details[0]
    assert isinstance(detail, OutcomeDetail)
    assert f"param:{sdi_capital.COMPOSITION_PARAM}" in detail.items
    # Production copy, not a parameter code: it must say no charge is assumed.
    assert "market-risk" in str(exc.value.detail)
    assert "operational-risk" in str(exc.value.detail)


def test_the_live_s29_view_still_computes_on_an_undetermined_scope(
    db_session: Session,
) -> None:
    """Indicative computes and LABELS; only the sealed run refuses.

    A management view of a provisional ratio is legitimate. Handing a regulator
    one is not. Both halves are asserted here so a future tightening cannot
    quietly take the live view away, and a future loosening cannot quietly give
    the filing path back.
    """
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])

    summary = sdi_capital.compute_sdi_capital_summary(db_session, _CTX, sdi, _AS_OF)

    assert summary.total_rwa_ghs == Decimal(_BOOK_GHS)
    assert summary.composition_source == sdi_capital.COMPOSITION_CODE_DEFAULT
    assert summary.composition_confirmed is False
    assert set(summary.excluded_risk_classes) == {"market", "operational"}
    assert "No market and operational risk charge is applied" in summary.rwa_scope_note
    # ...and the assurance surface still names it as a filing blocker.
    assurance = sdi_capital_assurance.get_sdi_capital_assurance(db_session, _CTX, sdi, _AS_OF)
    assert any("approved for this institution" in blocker for blocker in assurance.filing_blockers)


def test_a_governed_confirmed_scope_lets_the_official_run_proceed(
    db_session: Session,
) -> None:
    """The gate is a governance gate, not a prohibition on SDI filing.

    An approved, confirmed scope — whatever it declares — passes. This is the
    seam a Bank of Ghana determination plugs into with control-plane rows and no
    code change.
    """
    sdi = _sdi(db_session)
    _seed_capital(db_session, sdi, [("paid_up_capital", "20000000", "CET1")])
    _seed_positions(db_session, sdi, [("LN/1", "LOAN", "GHS", _BOOK_GHS, _BOOK_GHS)])
    _governed_scope(
        db_session, sdi, {"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE}
    )
    _add_param(
        db_session,
        param_code=sdi_capital.BUCKET_MAP_PARAM,
        value_json={"LOAN": "other_loans"},
        unit="count",
    )

    scope = sdi_capital.resolve_rwa_scope(db_session, sdi, _AS_OF)
    assert scope.source == sdi_capital.COMPOSITION_CONTROL_PLANE
    assert scope.confirmation_status == "confirmed"
    assert scope.filable is True

    sdi_capital.assert_official_rwa_scope_governed(db_session, sdi, _AS_OF)


def test_a_pending_scope_is_not_filable(db_session: Session) -> None:
    """Approved through four-eyes is not the same as confirmed against an
    instrument. A row still marked ``pending`` may drive the live view; it may
    not be sealed into a filing."""
    sdi = _sdi(db_session)
    db_session.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="sdi",
            param_code=sdi_capital.COMPOSITION_PARAM,
            jurisdiction_code="GH",
            value_json={"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE},
            unit="count",
            source_citation="Act 930 s.29(2) - statutory floor; no SDI RWA directive published",
            confirmation_status="pending",
            effective_from=date(2025, 1, 1),
            status="approved",
            proposed_by="test",
            approved_by="test-checker",
        )
    )
    db_session.flush()

    scope = sdi_capital.resolve_rwa_scope(db_session, sdi, _AS_OF)
    assert scope.confirmed is True
    assert scope.confirmation_status == "pending"
    assert scope.filable is False

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.assert_scope_filable(sdi, _AS_OF, scope)
    assert "pending confirmation" in str(exc.value.detail)


def test_an_unapproved_bucket_taxonomy_also_refuses_the_official_run(
    db_session: Session,
) -> None:
    """The other half of the same asserted-but-absent claim.

    ``bucket_map_source='code_default'`` has been documented as blocking filing
    since the taxonomy was made governed, and its only consumer was the same
    advisory read model. The default is not merely unapproved: the schedule it
    resembles is Form BSD 5A's proposal worksheet, a superseded BANK return.
    """
    sdi = _sdi(db_session)
    _governed_scope(
        db_session, sdi, {"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE}
    )

    _, source = sdi_capital.resolve_bucket_map(db_session, sdi, _AS_OF)
    assert source == sdi_capital.BUCKET_MAP_CODE_DEFAULT

    with pytest.raises(sdi_capital.SdiCapitalPolicyUnresolved) as exc:
        sdi_capital.assert_official_rwa_scope_governed(db_session, sdi, _AS_OF)
    assert sdi_capital.BUCKET_MAP_PARAM in str(exc.value.detail)


def test_a_bank_never_meets_the_sdi_scope_gate(db_session: Session) -> None:
    """``institution_class`` selects the legal regime, and the two share no
    formula. A bank's RWA scope is prescribed by the Capital Requirements
    Directive ¶73(a); there is nothing for the control plane to determine, and
    the gate must not manufacture a refusal for one."""
    bank = _bank(db_session, institution_type="universal_bank")

    sdi_capital.assert_official_rwa_scope_governed(db_session, bank, _AS_OF)

    active = regulatory_capital._load_active_params(  # pyright: ignore[reportPrivateUsage]
        db_session, _CTX, bank, _AS_OF
    )
    assert active.institution_class == "bank"
    # No SDI scope is resolved for a bank, so nothing about the Basel CRD path —
    # including its snapshot and therefore its input hash — can move.
    assert active.rwa_scope is None
    assert "sdi_rwa_composition" not in regulatory_capital._snapshot_parameters(  # pyright: ignore[reportPrivateUsage]
        active
    )


def test_the_sealed_run_records_who_determined_the_scope(db_session: Session) -> None:
    """The run "records no ``composition_source``" was half the D-19 finding.

    A reader of a sealed SDI run could not distinguish an approved credit-only
    scope from the platform's placeholder, because the snapshot carried the
    charges only when they moved a number and never the provenance of the
    declaration itself.
    """
    sdi = _sdi(db_session)
    _governed_scope(
        db_session, sdi, {"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE}
    )
    active = regulatory_capital._load_active_params(  # pyright: ignore[reportPrivateUsage]
        db_session, _CTX, sdi, _AS_OF
    )
    recorded = regulatory_capital._snapshot_parameters(active)[  # pyright: ignore[reportPrivateUsage]
        "sdi_rwa_composition"
    ]

    assert recorded == {
        "source": sdi_capital.COMPOSITION_CONTROL_PLANE,
        "confirmation_status": "confirmed",
        "composition": {"credit": sdi_capital.MEASURE_BUCKET_WEIGHTED_EXPOSURE},
    }
