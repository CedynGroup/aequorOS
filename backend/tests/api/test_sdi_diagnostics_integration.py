from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from tests.support.authorization_fixtures import (
    current_authorization_version,
    open_tenant_session,
    suspend_all_bindings,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, error_envelope, headers

USER_ID = USER_1


def _create_sdi_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="SDI Diagnostics",
            short_name="SDID",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def _create_universal_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="Universal Loan Diagnostics",
            short_name="ULD",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


#: One facility FIRST seen at the end of May, then re-observed month by month.
#: Only the June observation is a current, accepted snapshot: July is superseded,
#: August withdrawn, September rejected. The book's honest business date is June.
FIRST_SEEN = date(2026, 5, 31)
LATEST_CURRENT = date(2026, 6, 30)
SUPERSEDED_LATER = date(2026, 7, 31)
WITHDRAWN_LATER = date(2026, 8, 31)
REJECTED_LATER = date(2026, 9, 30)

#: The readiness rows, per governing module — the whole disclosure map of the
#: route. A caller sees exactly the rows of the modules it may view at
#: aggregated sensitivity; a module outside this map exists on no surface.
LIQUIDITY_ROWS = ("liquidity_table1", "maturity_ladder", "funding_concentration")
CAPITAL_ROWS = ("capital",)
CREDIT_ROWS = ("exposures", "provisioning")
ALL_ROWS = LIQUIDITY_ROWS + CAPITAL_ROWS + CREDIT_ROWS


def _module_only_viewer(module: ModuleScope) -> dict[str, str]:
    """Headers for an actor whose only active binding is one module's view.

    The hermetic fixture seeds the demo actor with an organization-wide
    all-module viewer binding, so a module-only actor is the same user with
    every binding suspended and exactly one aggregated viewer sentence granted
    back. Scalar roles are carried but grant nothing by themselves.
    """
    with get_sessionmaker()() as db:
        suspend_all_bindings(
            db, organization_id=ORG_1, user_id=USER_ID, reason="sdi module-view test"
        )
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_ID,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION, None, module, SensitivityScope.AGGREGATED
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "sdi-readiness-test"),
            reason="SDI readiness module-view regression authority",
        )
        db.commit()
    return _current_user_headers()


def _suspended_bindings_headers(roles: tuple[str, ...] = ("admin",)) -> dict[str, str]:
    """Headers for an actor with every binding suspended: no module view at all."""
    with get_sessionmaker()() as db:
        suspend_all_bindings(
            db, organization_id=ORG_1, user_id=USER_ID, reason="sdi no-binding test"
        )
        db.commit()
    return _current_user_headers(roles=roles)


def _current_user_headers(roles: tuple[str, ...] = ("admin",)) -> dict[str, str]:
    with open_tenant_session(ORG_1) as db:
        version = current_authorization_version(db, organization_id=ORG_1, user_id=USER_ID)
    return headers(roles=roles, authorization_version=version)


def _seed_snapshot_history(bank_id: str) -> None:
    """A LOAN whose position row is dated at its first sighting while its
    current snapshot is a month later, plus three LATER snapshots that no
    calculation may read (superseded / withdrawn / validation error)."""
    session = get_sessionmaker()()
    try:
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=bank_id,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=FIRST_SEEN,
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            bank_id=batch.bank_id,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="sdi-as-of-history",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        common = {
            "organization_id": ORG_1,
            "bank_id": bank_id,
            "source_system": "EXCEL_CSV",
            "source_reference": "LN-HISTORY",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
        }
        position = CanonicalPosition(
            **common,
            as_of_date=FIRST_SEEN,
            validation_status="accepted",
            position_type="LOAN",
            currency="GHS",
        )
        session.add(position)
        session.flush()
        observations = (
            (FIRST_SEEN, "accepted", None, None),
            (LATEST_CURRENT, "accepted", None, None),
            (SUPERSEDED_LATER, "accepted", uuid4(), None),
            (WITHDRAWN_LATER, "accepted", None, utc_now()),
            (REJECTED_LATER, "error", None, None),
        )
        for as_of, validation_status, superseded_by, withdrawn_at in observations:
            session.add(
                CanonicalPositionSnapshot(
                    **common,
                    as_of_date=as_of,
                    validation_status=validation_status,
                    position_id=position.id,
                    balance=Decimal("250000"),
                    ifrs9_stage=1,
                    superseded_by=superseded_by,
                    withdrawn_at=withdrawn_at,
                    withdrawn_by_batch_id=batch.id if withdrawn_at else None,
                    withdrawal_reason="duplicate feed" if withdrawn_at else None,
                    attributes={"balance_ghs": "250000", "days_past_due": 0},
                )
            )
        session.commit()
    finally:
        session.close()


def test_default_as_of_is_the_latest_current_snapshot_not_first_seen_or_a_retired_row(
    db_client,  # noqa: ANN001
) -> None:
    """With no ``as_of`` the diagnostics key on the newest date the CURRENT book
    carries: later than the position's first-seen date (May), and never a
    superseded, withdrawn or rejected snapshot (July, August, September)."""
    bank_id = _create_sdi_bank()
    _seed_snapshot_history(bank_id)

    classification = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/loan-classification", headers=headers()
    )
    assert classification.status_code == 200, classification.text
    assert classification.json()["as_of"] == LATEST_CURRENT.isoformat()
    # The June book is what gets classified, not an empty future date.
    assert classification.json()["loan_count"] == 1
    # A book with loans still reports its ratio — only an empty one withholds it.
    assert classification.json()["npl_ratio"] is not None

    readiness = db_client.get(f"/api/v1/banks/{bank_id}/sdi/readiness", headers=headers())
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["as_of"] == LATEST_CURRENT.isoformat()

    # An explicit as-of is still honoured verbatim.
    explicit = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/readiness",
        headers=headers(),
        params={"as_of": FIRST_SEEN.isoformat()},
    )
    assert explicit.json()["as_of"] == FIRST_SEEN.isoformat()


def test_empty_book_reports_no_as_of_and_no_npl_ratio(db_client) -> None:  # noqa: ANN001
    """A bank that has ingested nothing has no reporting date and no ratio.

    The route used to answer ``as_of=<today>`` with ``npl_ratio=0`` — a clean
    book measured on a date the bank never reported. Both figures are now
    withheld, and the grade grid is still listed so the surface can show which
    classification would apply (audit A1-09 / decision D-035).
    """
    bank_id = _create_sdi_bank()

    response = db_client.get(f"/api/v1/banks/{bank_id}/loan-classification", headers=headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] is None
    assert body["npl_ratio"] is None
    assert body["loan_count"] == 0
    # Nothing else is invented either: the unstated figures stay null.
    assert body["provisions_held"] is None
    assert body["provision_coverage_pct"] is None
    # The class grid is registry data, not a position, so it is still answered.
    assert body["institution_class"] == "sdi"


def test_sdi_liquidity_position_returns_typed_unavailable_controls(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/liquidity-position", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["ratios"]) == 8
    assert all(row["status"] == "not_computable" for row in body["ratios"])
    assert len(body["reserves"]) == 2
    assert all(row["status"] == "not_computable" for row in body["reserves"])
    assert body["maturity_ladder"]
    # The internal caller keeps the FULL readiness rows: its own gate (the
    # liquidity confidential resource) has already decided who is asking, so
    # the embedded report is never narrowed per module.
    assert [row["module"] for row in body["readiness"]] == list(ALL_ROWS)


def test_sdi_large_exposures_returns_empty_book_without_false_breach(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/large-exposures", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exposures"] == []
    assert body["findings"] == []


def test_sdi_capital_assurance_returns_explicit_filing_blockers(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/capital-assurance", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filing_status"] == "blocked"
    assert body["current"]["assessment_status"] == "not_computable"
    assert body["filing_blockers"]


def test_sdi_loan_classification_returns_raw_dpd_buckets(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dpd_covered_count"] == 0
    assert len(body["delinquency_buckets"]) == 7
    assert {metric["code"] for metric in body["portfolio_at_risk"]} == {
        "par_30",
        "par_60",
        "par_90",
        "par_180",
        "par_360",
    }
    # An empty book states no provisions: held is null and coverage is null —
    # the wire never fabricates a zero for an unstated figure.
    assert body["provisions_held"] is None
    assert body["provision_coverage_pct"] is None


def test_universal_bank_loan_classification_uses_neutral_endpoint(db_client) -> None:  # noqa: ANN001
    bank_id = _create_universal_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_class"] == "bank"
    assert "olem" in {bucket["grade"] for bucket in body["buckets"]}
    assert len(body["delinquency_buckets"]) == 7


def test_sdi_capital_summary_declares_the_risk_classes_it_omits(db_client) -> None:  # noqa: ANN001
    """The scope of the ratio reaches the wire, not just the service.

    A CAR computed on credit risk alone has to say so on the surface that
    presents it (forensic audit "DIVERGENCE #1"), so the payload carries every
    known risk class, in scope or not, and the one-line disclosure.
    """
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/capital-summary", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["composition_source"] == "code_default"
    by_class = {row["risk_class"]: row for row in body["risk_classes"]}
    assert set(by_class) == {"credit", "market", "operational"}
    assert by_class["credit"]["in_scope"] is True
    assert by_class["market"]["in_scope"] is False
    assert by_class["operational"]["in_scope"] is False
    assert "credit risk only" in body["rwa_scope_note"]
    # Reader-facing copy, no parameter codes or raw enums.
    assert "no charge is assumed" in by_class["operational"]["note"]


def _readiness_modules(client, bank_id: str, auth: dict[str, str]) -> list[str]:  # noqa: ANN001
    response = client.get(f"/api/v1/banks/{bank_id}/sdi/readiness", headers=auth)
    assert response.status_code == 200, response.text
    return [row["module"] for row in response.json()["modules"]]


def test_full_actor_still_sees_every_module_row(db_client) -> None:  # noqa: ANN001
    """The all-module viewer (the seeded demo sentence) keeps all six rows, in
    the service's own order — the filtering narrows, it never reorders."""
    bank_id = _create_sdi_bank()
    assert _readiness_modules(db_client, bank_id, headers()) == list(ALL_ROWS)


def test_liquidity_only_actor_sees_exactly_the_liquidity_rows(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    assert _readiness_modules(
        db_client, bank_id, _module_only_viewer(ModuleScope.LIQUIDITY)
    ) == list(LIQUIDITY_ROWS)


def test_capital_only_actor_sees_exactly_the_capital_row(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    assert _readiness_modules(
        db_client, bank_id, _module_only_viewer(ModuleScope.CAPITAL)
    ) == list(CAPITAL_ROWS)


def test_credit_only_actor_sees_exposures_and_provisioning(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    assert _readiness_modules(
        db_client, bank_id, _module_only_viewer(ModuleScope.CREDIT)
    ) == list(CREDIT_ROWS)


def test_actor_with_no_module_view_gets_an_empty_modules_list(db_client) -> None:  # noqa: ANN001
    """Zero qualifying rows answer 200 with an empty list, not a denial.

    The route filters what a caller may see rather than refusing the caller: a
    member of the organization without any module view still learns that there
    is nothing on this surface for them (the live summary's behavior), while a
    caller outside the organization keeps the opaque 404.
    """
    bank_id = _create_sdi_bank()
    response = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/readiness",
        headers=_suspended_bindings_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["modules"] == []


def test_account_admin_without_bindings_gets_an_empty_modules_list(db_client) -> None:  # noqa: ANN001
    """Scalar administration is not module view: the account admin sees no rows."""
    bank_id = _create_sdi_bank()
    response = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/readiness",
        headers=_suspended_bindings_headers(roles=("account_admin",)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["modules"] == []


def test_foreign_organization_bank_is_an_opaque_404(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/readiness",
        headers=headers(org_id=ORG_2),
    )
    assert response.status_code == 404, response.text
    assert error_envelope(response)["message"] == "Bank not found."
