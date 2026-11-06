"""Shared synthetic mart and scoped-reader helpers for BI route tests.

Fixtures are registered explicitly by their consuming test modules, not as a
global plugin. The shared mart is hand-built so route expectations remain
independent of the mart builder; expected figures can be derived from its rows.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.bi.models.marts import (
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiMartBuild,
)
from app.core import security
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.identity.service import authorization
from app.models import AuthorizationBinding, Bank, User
from tests.bi.route_data import (
    AS_OF,
    BANK_ID,
    BASE,
    BUILT_AT,
    CP_ONE,
    CP_TWO,
    FINGERPRINT,
    OBLIGOR_QUERY,
    OTHER_BANK_FINGERPRINT,
    OTHER_BANK_ROWS,
    SAME_TENANT_BANK_ID,
    SIBLING_BANK_ID,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

__all__ = [
    "AGGREGATE_ONLY",
    "AS_OF",
    "BASE",
    "CP_ONE",
    "OBLIGOR_QUERY",
    "Grant",
    "bi_on",
    "call",
    "grant_only",
    "impersonation_headers",
    "mart",
    "seed_bi_mart",
]


def _bank(db: Session, bank_id: str, organization_id: str) -> Bank:
    existing = db.get(Bank, bank_id)
    if existing is not None:
        return existing
    bank = Bank(
        id=bank_id,
        organization_id=organization_id,
        name=f"BI route bank {bank_id}",
        short_name="BI route",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _loan(  # noqa: PLR0913 - one keyword per column the assertions reason about
    bank: Bank,
    *,
    reference: str,
    branch: str,
    balance: Decimal,
    counterparty: UUID,
    non_performing: bool = False,
    position_type: str = "LOAN",
) -> BiFactPositionDaily:
    return BiFactPositionDaily(
        as_of_date=AS_OF,
        snapshot_id=uuid4(),
        position_id=uuid4(),
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_system="API_PUSH",
        source_reference=reference,
        position_type=position_type,
        currency=bank.currency,
        balance_native=balance,
        balance_rc=balance,
        fx_unconverted=False,
        classification_exposure_rc=balance if position_type == "LOAN" else None,
        non_performing=non_performing if position_type == "LOAN" else None,
        branch_code=branch,
        counterparty_id=counterparty,
        product_code="P-RETAIL",
        product_family="retail_loans",
        sector="trade",
        dpd_band="current",
        grade="standard",
        ifrs9_stage=1,
        builder_version=1,
        built_at=BUILT_AT,
    )


_AGG_GRAIN: tuple[str, ...] = (
    "position_type",
    "product_family",
    "branch_code",
    "currency",
    "ifrs9_stage",
    "dpd_band",
    "grade",
    "deposit_account_type",
)


def _aggregates(facts: list[BiFactPositionDaily]) -> list[BiAggPositionDaily]:
    """``bi_agg_position_daily`` by the contract's definition: additive sums per grain.

    The compiler reads the daily aggregate whenever every requested member is
    grain-local, so a fixture with facts and no aggregate answers a perfectly
    ordinary query with zero rows. Derived from the facts here rather than
    hand-written, so the two can never disagree.
    """

    groups: dict[tuple[Any, ...], list[BiFactPositionDaily]] = {}
    for fact in facts:
        key = tuple(getattr(fact, column) for column in _AGG_GRAIN)
        groups.setdefault(key, []).append(fact)

    def total(rows: list[BiFactPositionDaily], column: str) -> Decimal:
        return sum((getattr(row, column) or Decimal("0") for row in rows), Decimal("0"))

    out: list[BiAggPositionDaily] = []
    for key, rows in groups.items():
        first = rows[0]
        out.append(
            BiAggPositionDaily(
                as_of_date=AS_OF,
                id=uuid4(),
                organization_id=first.organization_id,
                bank_id=first.bank_id,
                **dict(zip(_AGG_GRAIN, key, strict=True)),
                row_count=len(rows),
                balance_rc_sum=total(rows, "balance_rc"),
                classification_exposure_rc_sum=total(rows, "classification_exposure_rc"),
                non_performing_exposure_rc_sum=sum(
                    (
                        row.classification_exposure_rc or Decimal("0")
                        for row in rows
                        if row.non_performing
                    ),
                    Decimal("0"),
                ),
                provision_required_rc_sum=total(rows, "provision_required_rc"),
                provision_held_rc_sum=total(rows, "provision_held_rc"),
                collateral_rc_sum=total(rows, "collateral_rc"),
                rate_x_balance_rc_sum=Decimal("0"),
                fx_unconverted_count=sum(1 for row in rows if row.fx_unconverted),
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    return out


def seed_bi_mart(db: Session) -> Bank:
    """One institution's mart for :data:`AS_OF`, plus a sibling tenant's bank.

    Loans: B1 carries 100 + 300 (the 300 non-performing), B2 carries 200. One
    deposit of 50 sits outside every loan measure. Two obligors, so a group by
    obligor name has something to hide.
    """

    bank = _bank(db, BANK_ID, ORG_1)
    _bank(db, SIBLING_BANK_ID, ORG_2)
    _bank(db, SAME_TENANT_BANK_ID, ORG_1)
    facts = [
        _loan(
            bank,
            reference="L-1",
            branch="B1",
            balance=Decimal("100"),
            counterparty=CP_ONE,
        ),
        _loan(
            bank,
            reference="L-2",
            branch="B1",
            balance=Decimal("300"),
            counterparty=CP_TWO,
            non_performing=True,
        ),
        _loan(
            bank,
            reference="L-3",
            branch="B2",
            balance=Decimal("200"),
            counterparty=CP_ONE,
        ),
        _loan(
            bank,
            reference="D-1",
            branch="B2",
            balance=Decimal("50"),
            counterparty=CP_ONE,
            position_type="DEPOSIT",
        ),
    ]
    db.add_all(facts)
    db.add_all(_aggregates(facts))
    for code, name in (("B1", "Head office"), ("B2", "Harbour branch")):
        db.add(
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code=code,
                name=name,
                region="Unassigned region",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    for counterparty_id, name in ((CP_ONE, "Ada Traders"), (CP_TWO, "Kwesi Holdings")):
        db.add(
            BiDimCounterparty(
                organization_id=ORG_1,
                bank_id=bank.id,
                counterparty_id=counterparty_id,
                source_reference=str(counterparty_id)[:8],
                name=name,
                counterparty_type="corporate",
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    db.add(
        BiDimProduct(
            organization_id=ORG_1,
            bank_id=bank.id,
            product_code="P-RETAIL",
            name="Retail term loan",
            product_family="retail_loans",
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    db.add(
        BiDimDate(
            organization_id=ORG_1,
            bank_id=bank.id,
            date=AS_OF,
            has_data=True,
            is_last_in_month=True,
            is_last_in_quarter=False,
            is_last_in_year=False,
            calendar_month=AS_OF.replace(day=1),
            calendar_quarter=dt.date(2026, 7, 1),
            calendar_year=2026,
            fiscal_year=2026,
            fiscal_quarter=3,
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    for tier in ("official", "live"):
        db.add(
            BiFactEngineMetric(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                module="capital",
                metric_id="car_pct",
                tier=tier,
                value=Decimal("14.25"),
                unit="pct",
                status="green",
                regime="crd",
                institution_class="bank",
                advisory_designation="filed",
                input_hash="a" * 64,
                engine_version="7",
                pipeline_state="ready",
                reconciliation_blocked=False,
                computed_at=BUILT_AT,
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    for scope in ("positions", "events", "gl", "engine", "dims"):
        db.add(
            BiMartBuild(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                scope=scope,
                fingerprint=FINGERPRINT,
                status="succeeded",
                builder_version=1,
                started_at=BUILT_AT,
                finished_at=BUILT_AT,
                row_counts={},
            )
        )
    # The SECOND institution of the same tenant carries build state of its own,
    # with values that appear nowhere else, so "the 403 leaked nothing" is a real
    # assertion rather than a statement about an empty table (audit A6-01).
    db.add(
        BiMartBuild(
            organization_id=ORG_1,
            bank_id=SAME_TENANT_BANK_ID,
            as_of_date=AS_OF,
            scope="positions",
            fingerprint=OTHER_BANK_FINGERPRINT,
            status="succeeded",
            builder_version=1,
            started_at=BUILT_AT,
            finished_at=BUILT_AT,
            row_counts={"bi_fact_position_daily": OTHER_BANK_ROWS},
        )
    )
    db.commit()
    return bank


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_bi_mart(db_session)


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    # Every surface these sweeps cover has to be ON, or a sweep silently stops
    # exercising the route it names: the natural-language routes answer 409 for the
    # flag before they reach authority. With it off, the zero-binding sweep in
    # tests.bi.api.test_bi_routes_integration would prove nothing about them.
    monkeypatch.setenv("BI_NLQ_ENABLED", "1")
    get_settings.cache_clear()


@dataclass(frozen=True, slots=True)
class Grant:
    module: ModuleScope
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED
    institution: InstitutionScope = InstitutionScope.INSTITUTION
    bundle: RoleBundle = RoleBundle.VIEWER


#: Enough to read loan totals by branch: the loan measures are CREDIT and the
#: branch dimension is RISK. Nothing here covers an obligor NAME (CREDIT /
#: restricted) or a position reference (RISK / confidential).
AGGREGATE_ONLY: tuple[Grant, ...] = (
    Grant(ModuleScope.CREDIT),
    Grant(ModuleScope.RISK),
)


def grant_only(db: Session, grants: tuple[Grant, ...]) -> int:
    """Replace the fixture's org-wide sentence with exactly ``grants``.

    Returns the principal's resulting ``authv``, which every token must carry:
    creating a binding invalidates the user's sessions by design.
    """

    db.execute(delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1))
    db.commit()
    for grant in grants:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=grant.bundle,
            scope=authorization.BindingScope(
                grant.institution,
                BANK_ID if grant.institution is InstitutionScope.INSTITUTION else None,
                grant.module,
                grant.sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the BI read surface.",
            commit=False,
        )
    db.commit()
    user = db.get(User, USER_1)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def impersonation_headers() -> dict[str, str]:
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=ORG_1,
        act_operator="ops@aequoros.com",
        session_id="11111111-2222-4333-8444-555555555555",
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    return {"Authorization": f"Bearer {token}"}


def call(  # noqa: PLR0913 - one request, spelled out
    client: TestClient,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    *,
    base: str = BASE,
    request_headers: dict[str, str] | None = None,
) -> Response:
    return client.request(
        method,
        f"{base}{suffix}",
        json=body,
        headers=request_headers if request_headers is not None else headers(),
    )
