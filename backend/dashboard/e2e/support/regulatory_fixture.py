"""Disposable persisted inputs for the regulatory-status browser journeys."""

import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authorization
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    LiveFinding,
    LiveMetric,
    RegulatoryMetricResult,
    RegulatoryParameter,
    RegulatoryRun,
    RegulatoryValidation,
    User,
)
from app.reporting.service.instrument_notifications import scan_instrument_statuses

database, action = sys.argv[1:3]
org, bank_id = "OR-DEM00001", "BK-SAMP0001"
with Session(create_engine(f"sqlite+pysqlite:///{database}")) as db:
    bank = db.get(Bank, bank_id)
    if action == "governance":
        db.add(
            Bank(
                id="BK-RULE0002",
                organization_id=org,
                name="Z Other Rule Institution",
                short_name="OTHER",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        for row in db.scalars(
            select(RegulatoryParameter).where(
                RegulatoryParameter.param_code == "icaap_report_first_as_of_date"
            )
        ):
            row.value_json = {"schema": "icaap-effective-date-v1", "date": "2028-12-31"}
            row.approved_by = "Disposable commencement approver"
            row.approved_at = datetime.now(UTC)
    elif action in ("KE", "NG", "GH"):
        bank.jurisdiction_code = action
        bank.currency = {"KE": "KES", "NG": "NGN", "GH": "GHS"}[action]
    elif action == "notify":
        ctx = TenantContext(organization_id=org)
        emitted = scan_instrument_statuses(db, ctx, bank, date(2026, 12, 2))
        repeat = scan_instrument_statuses(db, ctx, bank, date(2026, 12, 2))
        changed = scan_instrument_statuses(db, ctx, bank, date(2027, 1, 1))
        db.commit()
        print(
            json.dumps(
                {
                    "reminder_emitted": emitted,
                    "repeat_emitted": repeat,
                    "commencement_emitted": changed,
                }
            )
        )
        sys.exit()
    elif action == "forecast":
        period = db.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == bank_id)
            .order_by(BankReportingPeriod.period_end.desc())
        ).first()
        for breached, run_id in [
            (False, "bbbbbbbb-1111-4111-8111-111111111111"),
            (True, "bbbbbbbb-2222-4222-8222-222222222222"),
        ]:
            car = "8" if breached else "20"
            run = RegulatoryRun(
                id=UUID(run_id),
                organization_id=org,
                bank_id=bank_id,
                reporting_period_id=period.id,
                module="forecast",
                scenario_code="base",
                status="succeeded",
                engine_version="legacy-fixture",
                input_schema_version="1",
                output_schema_version="1",
                input_hash="a" * 64,
                inputs={},
                metrics={"year5_car_pct": car, "year5_lcr_pct": "80", "year5_nsfr_pct": "90"},
                created_by=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            )
            db.add(run)
            run.metrics = {
                **run.metrics,
                "avg_roe_pct": "12",
                "cumulative_net_income": "100",
                "min_car_pct": car,
                "min_lcr_pct": "80",
                "min_nsfr_pct": "90",
            }
            db.flush()
            for index, ratio in enumerate(("car", "lcr", "nsfr")):
                failed = ratio != "car" or breached
                db.add(
                    RegulatoryMetricResult(
                        organization_id=org,
                        bank_id=bank_id,
                        run_id=run.id,
                        metric_code=f"year5_{ratio}_pct",
                        metric_value=Decimal(
                            car if ratio == "car" else "80" if ratio == "lcr" else "90"
                        ),
                        unit="pct",
                        threshold_min=Decimal("13" if ratio == "car" else "100"),
                        status="red" if failed else "green",
                        position=index,
                    )
                )
                db.add(
                    RegulatoryValidation(
                        organization_id=org,
                        bank_id=bank_id,
                        run_id=run.id,
                        rule_code=f"year5_{ratio}_above_minimum",
                        passed=not failed,
                        severity="error",
                        message=f"Recorded {ratio.upper()} assessment",
                        position=index,
                    )
                )
        live = db.scalars(
            select(LiveMetric).where(LiveMetric.bank_id == bank_id, LiveMetric.module == "forecast")
        ).one()
        live.metrics = {
            "year5_car_pct": "8",
            "year5_lcr_pct": "80",
            "year5_nsfr_pct": "90",
            "year5_car_status": "red",
            "year5_lcr_status": "red",
            "year5_nsfr_status": "red",
        }
        live.status = "red"
        for ratio in ("car", "lcr", "nsfr"):
            db.add(
                LiveFinding(
                    organization_id=org,
                    bank_id=bank_id,
                    module="forecast",
                    rule_id=f"year5_{ratio}_above_minimum",
                    severity="critical",
                    message=f"Recorded {ratio.upper()} assessment",
                )
            )
    elif action == "sdi":
        sdi_id = "BK-RULESDI1"
        db.add(
            Bank(
                id=sdi_id,
                organization_id=org,
                name="A Disposable SDI",
                short_name="SDI",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="savings_and_loans",
                institution_type="savings_and_loans",
            )
        )
        db.flush()
        as_of = date(2026, 12, 31)
        batch = IngestionBatch(
            organization_id=org,
            bank_id=sdi_id,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=as_of,
        )
        db.add(batch)
        db.flush()
        lineage = LineageRecord(
            organization_id=org,
            bank_id=sdi_id,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="disposable-sdi-boundary",
            input_lineage_ids=[],
        )
        db.add(lineage)
        db.flush()
        common = dict(
            organization_id=org,
            bank_id=sdi_id,
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
            as_of_date=as_of,
        )
        db.add(
            CanonicalReferenceRow(
                **common,
                dataset_kind="capital_structure",
                row_index=0,
                source_reference="CS/1",
                payload={
                    "capital_component": "paid_up_capital",
                    "amount_ghs": "1000",
                    "tier": "CET1",
                },
            )
        )
        position = CanonicalPosition(
            **common,
            source_system="EXCEL_CSV",
            source_reference="SDI/LOAN/1",
            position_type="LOAN",
            currency="GHS",
        )
        db.add(position)
        db.flush()
        db.add(
            CanonicalPositionSnapshot(
                **common,
                source_system="EXCEL_CSV",
                source_reference="SDI/LOAN/1",
                validation_status="accepted",
                position_id=position.id,
                balance=Decimal("160"),
                attributes={"balance_ghs": "160", "issuer": "Disposable 16% obligor"},
            )
        )
    elif action == "sdi-commence":
        for row in db.scalars(
            select(CanonicalPositionSnapshot).where(
                CanonicalPositionSnapshot.bank_id == "BK-RULESDI1"
            )
        ):
            row.as_of_date = date(2027, 1, 1)
    elif action == "freeze-officer":
        officer = db.get(User, UUID("eeeeeeee-4444-4eee-8eee-eeeeeeeeeee4"))
        authorization.create_role_binding(
            db,
            organization_id=org,
            principal_user_id=officer.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.APPROVER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                bank_id,
                ModuleScope.CAPITAL,
                SensitivityScope.CONFIDENTIAL,
                data_scope=DataScope.ALL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "regulatory-journey"),
            reason="Separate fixture officer for the Ghana chain's final approval",
            commit=False,
        )
        db.commit()
        print(json.dumps({"authv": officer.authorization_version}))
        sys.exit()
    elif action == "immutable":
        runs = list(
            db.scalars(
                select(RegulatoryRun).where(RegulatoryRun.engine_version == "legacy-fixture")
            )
        )
        assert len(runs) == 2
        for run in runs:
            assert run.inputs == {} and run.input_hash == "a" * 64
            for ratio in ("lcr", "nsfr"):
                validation = db.scalars(
                    select(RegulatoryValidation).where(
                        RegulatoryValidation.run_id == run.id,
                        RegulatoryValidation.rule_code == f"year5_{ratio}_above_minimum",
                    )
                ).one()
                assert not validation.passed
                metric = db.scalars(
                    select(RegulatoryMetricResult).where(
                        RegulatoryMetricResult.run_id == run.id,
                        RegulatoryMetricResult.metric_code == f"year5_{ratio}_pct",
                    )
                ).one()
                assert metric.status == "red"
        print(
            json.dumps(
                {
                    "saved_evidence_unchanged": True,
                    "stored_reference_assessments": "red",
                    "stored_liquidity_validations": "failed",
                }
            )
        )
    else:
        raise ValueError(action)
    db.commit()
