"""Bank-local CREDIT authority cannot change a sibling's registers or outputs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.capital.service.regulatory_capital import (
    _load_active_params,  # pyright: ignore[reportPrivateUsage] - pin the engine resolution seam
)
from app.core.authorization import ModuleScope, RoleBundle
from app.credit.service import credit_concentration, credit_params
from app.credit.service.regulatory_credit import (
    _board_thresholds,  # pyright: ignore[reportPrivateUsage] - pin the engine resolution seam
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    Bank,
    CurrentFinancialFact,
    Job,
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    User,
)
from app.policy.public import get_active_bank_params, get_active_params, prefetch_active_params
from tests.support.bank_seeding import seed_bank
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

SIBLING = "BK-LOCREDIT02"
AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
PATHS = ("ecl-assumptions", "crm-haircuts", "concentration-limits", "credit-thresholds")
LOCAL_MODELS = (
    ParamConcentrationLimit,
    ParamCreditThreshold,
    ParamEclAssumption,
    ParamCrmHaircut,
)
CONFIGURATOR = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc2")

_PAYLOADS: dict[str, dict[str, JsonValue]] = {
    "ecl-assumptions": {
        "assumptions": [{"segment": "ALL", "stage": 1, "pd_pct": "5", "lgd_pct": "44"}],
        "effective_from": "2026-04-01",
    },
    "crm-haircuts": {"haircuts": {"CASH": "9"}, "effective_from": "2026-04-01"},
    "concentration-limits": {
        "limits": [
            {
                "dimension": "sector",
                "limit_kind": "share_of_book_pct",
                "bucket_key": None,
                "value": "20",
            }
        ],
        "effective_from": "2026-04-01",
    },
    "credit-thresholds": {
        "thresholds": {"npl_board_trigger_pct": "9"},
        "effective_from": "2026-04-01",
    },
}


def _bank(db: Session, bank_id: str, org: str = ORG_1, country: str = "GH") -> Bank:
    bank = Bank(
        id=bank_id,
        organization_id=org,
        name="Credit scope fixture",
        short_name="Credit",
        currency="NGN" if country == "NG" else "GHS",
        jurisdiction_code=country,
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _policy(db: Session, bank: Bank) -> None:
    """Two existing generations, deliberately identical keys across sibling banks."""
    for start, end, value in (
        (date(2026, 1, 1), date(2026, 3, 1), "110"),
        (date(2026, 3, 1), None, "120"),
    ):
        common = {
            "organization_id": bank.organization_id,
            "bank_id": bank.id,
            "jurisdiction_code": bank.jurisdiction_code,
            "effective_from": start,
            "effective_to": end,
            "approved_by": "Existing Board",
            "approval_timestamp": utc_now(),
        }
        db.add(
            ParamEclAssumption(
                **common,
                segment="ALL",
                stage=1,
                pd_pct=Decimal(value) / Decimal("40"),
                lgd_pct=Decimal("40"),
            )
        )
        db.add(ParamCrmHaircut(**common, collateral_class="CASH", haircut_pct=Decimal(value) / 10))
        db.add(
            ParamConcentrationLimit(
                **common,
                dimension="sector",
                limit_kind="share_of_book_pct",
                bucket_key=None,
                value=Decimal(value),
            )
        )
        db.add(
            ParamCreditThreshold(
                **common, threshold_code="npl_board_trigger_pct", value_pct=Decimal(value)
            )
        )
    db.add(
        CurrentFinancialFact(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            source_as_of_date=AS_OF,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            amount=Decimal("1"),
            currency=bank.currency,
        )
    )
    db.flush()


def _grant_configure(db: Session, bank_id: str, user_id: UUID = USER_1) -> None:
    grant_institution_authority(
        db,
        organization_id=ORG_1,
        bank_id=bank_id,
        user_id=user_id,
        bundle=RoleBundle.CONFIGURATOR,
        module=ModuleScope.CREDIT,
    )


def _engine_state(bank_id: str) -> tuple[str, str, dict[str, str], str]:
    """The bank's resolved registers and the capital engine's ECL/CRM inputs."""
    with get_sessionmaker()() as db:
        bank = db.get(Bank, bank_id)
        assert bank is not None
        crm = credit_params.get_crm_register(db, CTX, bank_id, AS_OF)
        ecl = credit_params.get_ecl_register(db, CTX, bank_id, AS_OF)
        limits = credit_concentration.active_limits(db, CTX, bank, AS_OF)
        thresholds = _board_thresholds(db, CTX, bank, AS_OF)
        params = _load_active_params(db, CTX, bank, AS_OF)
        return (
            crm.model_dump_json(),
            ecl.model_dump_json(),
            {limit.dimension: str(limit.value) for limit in limits},
            repr(
                (
                    sorted(params.crm_haircuts.items()),
                    sorted(
                        (row.segment, row.stage, str(row.pd_pct), str(row.lgd_pct))
                        for row in params.ecl_assumptions
                    ),
                )
            )
            + str(sorted(thresholds.items())),
        )


def test_bank_a_write_leaves_sibling_registers_and_engine_policy_unchanged(
    db_client: TestClient,
) -> None:
    bank_a = seed_bank(db_client)
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        sibling = _bank(db, SIBLING)
        bank = db.get(Bank, bank_a)
        assert bank is not None
        _policy(db, bank)
        _policy(db, sibling)
        db.commit()
    before_engine = _engine_state(SIBLING)
    before_reads: dict[str, str] = {}
    for path in PATHS:
        response = db_client.get(f"/api/v1/banks/{SIBLING}/{path}?as_of={AS_OF}", headers=headers())
        assert response.status_code == 200, response.text
        before_reads[path] = response.text
    actor = _configure_headers(bank_a)
    for path in PATHS:
        request = TypeAdapter(dict[str, JsonValue]).validate_python(
            {
                **_PAYLOADS[path],
                "approved_by": "Bank A Board",
                "reason": "Bank A only annual policy",
            }
        )
        allowed = db_client.put(f"/api/v1/banks/{bank_a}/{path}", headers=actor, json=request)
        assert allowed.status_code == 200, allowed.text
        denied = db_client.put(f"/api/v1/banks/{SIBLING}/{path}", headers=actor, json=request)
        assert denied.status_code == 403, denied.text
    assert _engine_state(SIBLING) == before_engine
    assert _engine_state(bank_a) != before_engine
    for path, before in before_reads.items():
        response = db_client.get(f"/api/v1/banks/{SIBLING}/{path}?as_of={AS_OF}", headers=headers())
        assert response.status_code == 200, response.text
        assert response.text == before
    with get_sessionmaker()() as db:
        # Supersession and invalidation must not fan out to the sibling with real inputs.
        assert set(db.scalars(select(Job.bank_id).where(Job.job_type == "pipeline_refresh"))) == {
            bank_a,
        }
        for model in LOCAL_MODELS:
            rows = db.scalars(
                select(model).where(
                    model.bank_id == bank_a, model.effective_from == date(2026, 3, 1)
                )
            ).all()
            assert rows and all(row.effective_to == date(2026, 4, 1) for row in rows)
            sibling_rows = db.scalars(
                select(model).where(
                    model.bank_id == SIBLING, model.effective_from == date(2026, 3, 1)
                )
            ).all()
            assert sibling_rows and all(row.effective_to is None for row in sibling_rows)


def _configure_headers(bank_id: str) -> dict[str, str]:
    """A dedicated fixture officer holding the exact CREDIT configure sentence."""
    from sqlalchemy import or_ as _or  # noqa: PLC0415 - helper-local import mirrors LIQ helpers

    officer = CONFIGURATOR
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        if db.get(User, officer) is None:
            db.add(
                User(
                    id=officer,
                    organization_id=ORG_1,
                    email="credit-configurator@example.test",
                    display_name="Credit configuration fixture officer",
                )
            )
            db.flush()
        now = utc_now()
        existing = db.scalar(
            select(AuthorizationBinding.id)
            .where(
                AuthorizationBinding.organization_id == ORG_1,
                AuthorizationBinding.principal_user_id == officer,
                AuthorizationBinding.principal_type == "human",
                AuthorizationBinding.role_bundle == RoleBundle.CONFIGURATOR.value,
                AuthorizationBinding.institution_scope == "institution",
                AuthorizationBinding.institution_id == bank_id,
                AuthorizationBinding.module_scope == ModuleScope.CREDIT.value,
                AuthorizationBinding.sensitivity_scope == "restricted",
                AuthorizationBinding.data_scope_kind == "all",
                AuthorizationBinding.status == "active",
                AuthorizationBinding.revoked_at.is_(None),
                AuthorizationBinding.valid_from <= now,
                _or(
                    AuthorizationBinding.valid_until.is_(None),
                    AuthorizationBinding.valid_until > now,
                ),
            )
            .limit(1)
        )
        if existing is None:
            _grant_configure(db, bank_id, officer)
        db.commit()
    return headers(user_id=officer, roles=("viewer",))


@pytest.mark.parametrize(
    ("org", "country"),
    [
        (ORG_1, "GH"),
        (ORG_1, "NG"),
        (ORG_2, "GH"),
    ],
)
def test_policy_resolver_requires_complete_bank_scope(
    db_client: TestClient,
    org: str,
    country: str,
) -> None:
    bank_a = seed_bank(db_client)
    with get_sessionmaker()() as db:
        db.info["organization_id"] = org
        bank = _bank(db, SIBLING, org, country)
        _policy(db, bank)
        db.commit()
        ctx = TenantContext(organization_id=org)
        assert _board_thresholds(db, ctx, bank, AS_OF) == {"npl_board_trigger_pct": Decimal("120")}
        assert get_active_bank_params(
            db, ORG_1, bank_a, "GH", ParamCreditThreshold, AS_OF
        ) == []
        assert (
            get_active_bank_params(
                db,
                ORG_1,
                SIBLING,
                "NG" if country == "GH" else "GH",
                ParamCreditThreshold,
                AS_OF,
            )
            == []
        )
        if org != ORG_1:
            assert (
                get_active_bank_params(
                    db,
                    ORG_1,
                    SIBLING,
                    country,
                    ParamCreditThreshold,
                    AS_OF,
                )
                == []
            )
        for model in LOCAL_MODELS:
            with pytest.raises(ValueError, match="require bank_id"):
                get_active_params(db, org, country, model, AS_OF)
            with pytest.raises(ValueError, match="require bank_id"):
                prefetch_active_params(db, org, country, model, [])
            rows = get_active_params(db, org, country, model, AS_OF, bank_id=SIBLING)
            assert rows and all(row.bank_id == SIBLING for row in rows)
            assert (
                prefetch_active_params(db, org, country, model, [AS_OF], bank_id=SIBLING).active_on(
                    AS_OF
                )
                == rows
            )
        before_registers = [
            credit_params.get_ecl_register(db, ctx, SIBLING, AS_OF).model_dump_json(),
            credit_params.get_crm_register(db, ctx, SIBLING, AS_OF).model_dump_json(),
        ]
        actor = _configure_headers(bank_a)
        for path in PATHS:
            response = db_client.put(
                f"/api/v1/banks/{bank_a}/{path}",
                json={
                    **_PAYLOADS[path],
                    "approved_by": "Bank A Board",
                    "reason": "Bank A only",
                },
                headers=actor,
            )
            assert response.status_code == 200, response.text
        db.expire_all()
        assert (
            credit_params.get_ecl_register(db, ctx, SIBLING, AS_OF).model_dump_json()
            == before_registers[0]
        )
        assert (
            credit_params.get_crm_register(db, ctx, SIBLING, AS_OF).model_dump_json()
            == before_registers[1]
        )
        assert _board_thresholds(db, ctx, bank, AS_OF) == {"npl_board_trigger_pct": Decimal("120")}
