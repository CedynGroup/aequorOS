"""The cutover gate reports exactly what the evaluator projects — nothing inferred."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import Engine, String, Table, event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authorization
from app.main import app
from app.models import AuthorizationBinding, Bank, Organization, User
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from scripts import authorization_configure_impact as configure_impact
from scripts.authorization_access_impact import (
    INSTITUTION_MODULES,
    UserAccess,
    build_report,
    render_table,
)
from scripts.authorization_configure_impact import (
    ConfigureReport,
    build_configure_report,
    write_artifacts,
)
from scripts.authorization_configure_routes import CONFIGURE_ROUTES
from tests.support.database import isolated_database

ORG = "OR-IMPCT001"
BANK = "BK-IMPCT001"
OTHER_BANK = "BK-IMPCT002"


def _configure_mismatch_cases() -> list[tuple[str, dict[str, object]]]:
    """Each single-dimension corruption of an exact configurator credit binding."""
    now = datetime.now(UTC)
    return [
        ("wrong-module", {"module_scope": "data"}),
        ("wrong-sensitivity", {"sensitivity_scope": "confidential"}),
        ("wrong-role", {"role_bundle": "viewer"}),
        ("wrong-institution", {"institution_id": OTHER_BANK}),
        ("expired", {"valid_until": now - timedelta(days=1)}),
        ("not-yet-valid", {"valid_from": now + timedelta(days=1)}),
        ("suspended", {"status": "suspended"}),
        (
            "revoked",
            {
                "status": "revoked",
                "revoked_at": now,
                "revoked_by_type": "system",
                "revoked_by_id": "test",
                "revoked_reason": "test",
            },
        ),
        (
            "branch-scoped",
            {"data_scope_kind": DataScope.BRANCH.value, "data_scope_values": ["B1"]},
        ),
    ]


CONFIGURE_MISMATCH_CASES = _configure_mismatch_cases()


def _mismatched_binding_values(user: User, now: datetime, case: str) -> dict[str, object]:
    """The exact configurator credit binding each mismatch case corrupts in one dimension."""
    return {
        "id": uuid4(),
        "organization_id": ORG,
        "principal_user_id": user.id,
        "principal_type": "human",
        "role_bundle": "configurator",
        "institution_scope": "institution",
        "institution_id": BANK,
        "module_scope": "credit",
        "sensitivity_scope": "restricted",
        "granted_by_type": "system",
        "granted_by_id": f"test:{case}",
        "grant_reason": "impact matrix",
        "valid_from": now - timedelta(days=2),
    }


def _rendered_cells(rows: list[UserAccess]) -> dict[str, dict[str, str]]:
    """The rendered table parsed back into header-keyed cells per email.

    A column-level assertion is what the operator actually reads: a table-wide
    containment check passes through an unrelated column (the auditor email also
    appears under ``product view``), so it cannot prove the column it names.
    """
    lines = render_table(rows).splitlines()
    header = re.split(r"\s{2,}", lines[0].strip())
    parsed: dict[str, dict[str, str]] = {}
    for line in lines[1:]:
        if set(line.strip()) == {"-"}:
            continue
        parsed_cell = dict(zip(header, re.split(r"\s{2,}", line.strip()), strict=True))
        parsed[parsed_cell["email"]] = parsed_cell
    return parsed


def _user(db: Session, email: str, *, role: str = "viewer", **overrides) -> User:
    user = User(
        id=uuid4(),
        organization_id=ORG,
        email=email,
        display_name=email.split("@", maxsplit=1)[0].replace(".", " ").title(),
        role=role,
        auth_provider="password",
        is_active=True,
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    db.add(user)
    return user


def _grant(  # noqa: PLR0913 - one indivisible sentence per call
    db: Session,
    user: User,
    bundle: RoleBundle,
    *,
    institution_scope: InstitutionScope = InstitutionScope.ORGANIZATION,
    institution_id: str | None = None,
    module_scope: ModuleScope = ModuleScope.ALL,
    sensitivity_scope: SensitivityScope = SensitivityScope.ALL,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            institution_scope, institution_id, module_scope, sensitivity_scope
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test:impact"),
        reason="impact report fixture",
    )


def test_report_flags_users_the_dashboard_would_show_nothing(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Compare real bindings and CLI evidence without deriving access from scalar roles."""
    db_session.add(Organization(id=ORG, name="Impact proof"))
    db_session.add(
        Bank(
            id=BANK,
            organization_id=ORG,
            name="Impact Bank",
            short_name="Impact",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    owner = _user(db_session, "owner@impact.example", role="account_admin")
    reader = _user(db_session, "reader@impact.example")
    liquidity_only = _user(db_session, "liquidity@impact.example", role="analyst")
    _user(db_session, "nobody@impact.example")
    _user(db_session, "gone@impact.example", is_active=False)
    feed = _user(db_session, "feed@impact.example", role="analyst", auth_provider="service")
    db_session.commit()

    _grant(db_session, owner, RoleBundle.ORG_OWNER, module_scope=ModuleScope.ACCOUNT)
    _grant(db_session, reader, RoleBundle.VIEWER)
    authorization.create_role_binding(
        db_session,
        organization_id=ORG,
        principal_user_id=feed.id,
        principal_type=PrincipalType.MACHINE,
        role_bundle=RoleBundle.INTEGRATION_WRITER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, BANK, ModuleScope.DATA, SensitivityScope.RESTRICTED
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test:impact"),
        reason="machine inventory",
    )
    _grant(
        db_session,
        liquidity_only,
        RoleBundle.ANALYST,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    db_session.commit()

    rows = {row.email: row for row in build_report(db_session, organization_id=ORG)}

    # Inactive and machine principals are not sign-in identities: excluded.
    assert set(rows) == {
        "owner@impact.example",
        "reader@impact.example",
        "liquidity@impact.example",
        "nobody@impact.example",
    }

    owner_row = rows["owner@impact.example"]
    assert owner_row.account_administer is True
    assert owner_row.institutions == {}
    assert owner_row.flags == ["no_product_view", "account_plane_only"]

    reader_row = rows["reader@impact.example"]
    assert reader_row.account_administer is False
    assert reader_row.institutions == {BANK: sorted(INSTITUTION_MODULES)}
    assert reader_row.flags == []

    liquidity_row = rows["liquidity@impact.example"]
    assert liquidity_row.institutions == {BANK: ["liq"]}
    assert liquidity_row.flags == []

    nobody_row = rows["nobody@impact.example"]
    assert nobody_row.active_bindings == 0
    assert nobody_row.flags == ["no_bindings", "no_product_view"]
    # The scalar role is reported for context only; it never produces access.
    assert nobody_row.scalar_role == "viewer"

    # Nobody in this fixture can file: the Validator bundle is the only carrier
    # of `submit`, and no migration or scalar role produces it.
    assert all(row.filing_institutions == [] for row in rows.values())

    table = render_table(list(rows.values()))
    assert "account_plane_only" in table
    assert f"{BANK}: all" in table
    assert f"{BANK}: liq" in table
    assert "no_bindings,no_product_view" in table
    _assert_configure_cutover(db_session, owner, liquidity_only)
    _assert_configure_lifecycle(db_session)
    _assert_configure_artifacts(db_session, tmp_path, monkeypatch, capsys)
    _assert_configure_evaluator_failure(db_session, monkeypatch)


def _assert_configure_cutover(db: Session, owner: User, analyst: User) -> None:
    """The same cutover inventory must include machines and never infer grants."""
    before = build_configure_report(db, organization_id=ORG)
    assert len(CONFIGURE_ROUTES) == 31
    assert len({(route.method, route.path) for route in CONFIGURE_ROUTES}) == 31
    mounted = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert {(route.method, route.path) for route in CONFIGURE_ROUTES} <= mounted
    assert {route.module.value for route in CONFIGURE_ROUTES} == {
        "liq",
        "credit",
        "data",
        "markets",
        "reg",
        "beh",
    }
    assert not any(
        route.path.endswith(("/test", "/schema", "/sync", "/pull")) for route in CONFIGURE_ROUTES
    )
    machine = next(user for user in before.principals if user.principal_type == "machine")
    machine_impacts = [row for row in before.impacts if row.principal_id == machine.principal_id]
    assert machine_impacts
    assert all(not row.before.allowed and not row.after.allowed for row in machine_impacts)
    assert all(row.after.reason == "human_session_required" for row in machine_impacts)
    assert all(row.required_grant is None for row in machine_impacts)
    assert any(
        row.principal_id == machine.principal_id
        and row.principal_type == "machine"
        and row.role_bundle == "integration_writer"
        for row in before.existing_grants
    )
    assert all(not row.after.allowed for row in before.impacts)
    analyst_impacts = [row for row in before.impacts if row.principal_id == str(analyst.id)]
    assert any(row.newly_denied for row in analyst_impacts if row.module == "data")
    assert all(not row.before.allowed for row in analyst_impacts if row.module == "liq")
    owner_org = [
        row
        for row in before.impacts
        if row.principal_id == str(owner.id) and row.institution_scope == "organization"
    ]
    assert len(owner_org) == 2
    assert all(row.before.allowed and row.newly_denied for row in owner_org)

    _grant(
        db,
        analyst,
        RoleBundle.CONFIGURATOR,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    _grant(
        db,
        owner,
        RoleBundle.CONFIGURATOR,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    db.commit()
    after = build_configure_report(db, organization_id=ORG)
    allowed = [
        row for row in after.impacts if row.principal_id == str(analyst.id) and row.after.allowed
    ]
    assert len(allowed) == 3
    assert all(row.module == "liq" and row.after.matching_binding_ids for row in allowed)
    assert all(row.required_grant is None for row in allowed)
    needed = next(
        row.required_grant
        for row in after.impacts
        if row.principal_id == str(analyst.id) and row.module == "credit"
    )
    assert needed is not None
    assert (
        needed.organization_id,
        needed.principal_id,
        needed.principal_type,
        needed.institution_scope,
        needed.institution_id,
        needed.module_scope,
        needed.sensitivity_scope,
        needed.role_bundle,
        needed.data_scope_kind,
        needed.status,
    ) == (
        ORG,
        str(analyst.id),
        "human",
        "institution",
        BANK,
        "credit",
        "restricted",
        "configurator",
        "all",
        "active",
    )
    assert needed.approval_required is True
    owner_allowed = [
        row
        for row in after.impacts
        if row.principal_id == str(owner.id) and row.institution_scope == "organization"
    ]
    assert len(owner_allowed) == 2 and all(row.after.allowed for row in owner_allowed)
    # A bank grant never grants either organization-targeted settings action.
    assert all(
        not row.after.allowed
        for row in after.impacts
        if row.principal_id == str(analyst.id) and row.institution_scope == "organization"
    )
    assert any(row.role_bundle == "configurator" for row in after.existing_grants)
    assert not db.new and not db.dirty and not db.deleted


def _assert_configure_lifecycle(db: Session) -> None:
    """Wrong dimensions, incomplete rows and ineffective lifecycle never compose."""
    now = datetime.now(UTC)
    user = _user(db, "matrix@impact.example", role="approver")
    db.add(
        Bank(
            id=OTHER_BANK,
            organization_id=ORG,
            name="Other bank",
            short_name="Other",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    db.commit()
    # Insert every mismatch case as pooled evidence; each case is asserted on its
    # own by test_one_mismatched_dimension_denies_configure_credit below.
    db.add_all(
        AuthorizationBinding(**(_mismatched_binding_values(user, now, case) | overrides))
        for case, overrides in CONFIGURE_MISMATCH_CASES
    )
    db.commit()
    report = build_configure_report(db, organization_id=ORG, now=now)
    rows = [
        row
        for row in report.impacts
        if row.principal_id == str(user.id)
        and row.module == "credit"
        and row.institution_id == BANK
    ]
    assert rows and all(row.before.allowed and not row.after.allowed for row in rows)
    assert all(row.required_grant is not None for row in rows)
    stored = [row for row in report.existing_grants if row.principal_id == str(user.id)]
    assert {row.status for row in stored} == {"active", "suspended", "revoked"}
    assert any(not row.effective for row in stored)
    # All existing rows remain evidence, including narrowed rows and validity windows.
    assert any(
        row.data_scope_kind == "branch" and row.data_scope_values == ["B1"] for row in stored
    )
    # The fixture's other tenants and banks never enter this tenant's inventory.
    assert {row.organization_id for row in report.principals} == {ORG}
    assert {row.institution_id for row in report.impacts} == {BANK, OTHER_BANK, None}


@pytest.mark.parametrize(
    "overrides",
    [pytest.param(overrides, id=case) for case, overrides in CONFIGURE_MISMATCH_CASES],
)
def test_one_mismatched_dimension_denies_configure_credit(
    db_session: Session,
    overrides: dict[str, object],
) -> None:
    """Each mismatch is its own reported case and never composes into configure authority."""
    now = datetime.now(UTC)
    db_session.add(Organization(id=ORG, name="Impact matrix"))
    user = _user(db_session, "matrix@impact.example", role="approver")
    db_session.add_all(
        Bank(
            id=bank_id,
            organization_id=ORG,
            name=name,
            short_name=name,
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
        for bank_id, name in ((BANK, "Impact Bank"), (OTHER_BANK, "Other bank"))
    )
    db_session.commit()
    db_session.add(
        AuthorizationBinding(**(_mismatched_binding_values(user, now, "case") | overrides))
    )
    db_session.commit()
    report = build_configure_report(db_session, organization_id=ORG, now=now)
    rows = [
        row
        for row in report.impacts
        if row.principal_id == str(user.id)
        and row.module == "credit"
        and row.institution_id == BANK
    ]
    assert rows and all(not row.after.allowed for row in rows)
    assert all(row.required_grant is not None for row in rows)


def _read_json(path: Path) -> dict[str, object]:
    # Test-only JSON file boundary; the producer's dataclass schema is checked below.
    return cast(dict[str, object], json.loads(path.read_text()))


def _assert_artifact_content(report: ConfigureReport, directory: Path) -> None:
    """Verify consistent, private and non-overwritable policy assessment artifacts."""
    before = _read_json(directory / "configure-before.json")
    after = _read_json(directory / "configure-after.json")
    grants = _read_json(directory / "configure-exact-grants.json")
    assert before["stage"] == "before" and after["stage"] == "after"
    assert before["generated_at"] == after["generated_at"] == grants["generated_at"]
    assert before["principals"] == after["principals"] == grants["principals"]
    assert before["outcomes"] != after["outcomes"]
    assert grants["existing_grants"] == report.to_dict()["existing_grants"]
    required = grants["required_grants"]
    assert isinstance(required, list) and required
    assert "approval_required" in json.dumps(required)
    for path in directory.glob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
        content = path.read_text()
        assert "@impact.example" not in content
        assert "password_hash" not in content and "grant_reason" not in content
    with pytest.raises(FileExistsError):
        write_artifacts(report, directory)
    assert _read_json(directory / "configure-after.json") == after


def _assert_configure_artifacts(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise successful CLI output and collision refusal."""
    report = build_configure_report(db, organization_id=ORG)
    destination = tmp_path / "configure"
    write_artifacts(report, destination)
    _assert_artifact_content(report, destination)
    evidence = os.getenv("CONFIGURE_IMPACT_EVIDENCE_DIR")
    if evidence:
        write_artifacts(report, Path(evidence))

    bind = db.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine

    @contextmanager
    def session_boundary(_engine: Engine, *, autoflush: bool) -> Generator[Session]:
        assert autoflush is False
        yield db

    def engine_boundary(_url: str) -> Engine:
        return engine

    with monkeypatch.context() as patch:
        patch.setattr(configure_impact, "create_engine", engine_boundary)
        patch.setattr(configure_impact, "Session", session_boundary)
        patch.setattr(engine, "dispose", lambda: None)
        assert configure_impact.main(["--database-url", "sqlite://", "--organization", ORG]) == 0
        output = capsys.readouterr()
        assert '"schema_version": 1' in output.out and not output.err
        assert configure_impact.main(["--database-url", "sqlite://"]) == 0
        output = capsys.readouterr()
        assert '"schema_version": 1' in output.out and not output.err
        cli_dir = tmp_path / "cli"
        assert (
            configure_impact.main(
                [
                    "--database-url",
                    "sqlite://",
                    "--organization",
                    ORG,
                    "--output-dir",
                    str(cli_dir),
                ]
            )
            == 0
        )
        _assert_artifact_content(report, cli_dir)
        assert (
            configure_impact.main(
                [
                    "--database-url",
                    "sqlite://",
                    "--organization",
                    ORG,
                    "--output-dir",
                    str(cli_dir),
                ]
            )
            == 2
        )
        assert "no complete assessment" in capsys.readouterr().err


def test_configure_standalone_import_registers_tenant_rls_handling() -> None:
    """A fresh CLI interpreter must not depend on an incidental app.main import."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from sqlalchemy import event\n"
            "from sqlalchemy.orm import Session\n"
            "import scripts.authorization_configure_impact\n"
            "from app.db.session import set_tenant_rls_context\n"
            "assert event.contains(Session, 'after_begin', set_tenant_rls_context)\n",
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "explicit_output",
    [False, True],
    ids=["default-output-dir", "explicit-output-dir"],
)
def test_configure_empty_inventory_publishes_no_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    explicit_output: bool,
) -> None:
    """An invisible tenant inventory must not publish successful evidence in either mode."""
    url = f"sqlite:///{tmp_path / 'empty-organizations.sqlite'}"
    with isolated_database(url) as database:
        table = Organization.__table__
        assert isinstance(table, Table)
        table.create(database.engine())
    destination = tmp_path / "empty-inventory"
    output_args = ["--output-dir", str(destination)] if explicit_output else []
    assert configure_impact.main(["--database-url", url, *output_args]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "no complete assessment" in output.err
    assert not destination.exists()


def _assert_configure_evaluator_failure(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluator exceptions remain failed decisions without exposing sensitive details."""

    def failed(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("sensitive evaluator detail must not enter the artifact")

    with monkeypatch.context() as patch:
        patch.setattr(authorization, "evaluate_prefetched_permission", failed)
        report = build_configure_report(db, organization_id=ORG)
    assert report.evaluation_failed
    assert all(not row.after.allowed for row in report.impacts)
    assert any(row.after.reason == "evaluation_failed" for row in report.impacts)


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="Snapshot interleaving requires independent PostgreSQL connections.",
)
@pytest.mark.committing_db
def test_configure_inventory_uses_one_read_only_snapshot(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A concurrent atomic change must not split principal and grant evidence."""
    db_session.add(Organization(id=ORG, name="Snapshot impact"))
    user = _user(db_session, "snapshot@impact.example")
    db_session.commit()
    _grant(db_session, user, RoleBundle.CONFIGURATOR, module_scope=ModuleScope.REGULATORY)
    db_session.commit()
    binding = db_session.scalar(
        select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == user.id)
    )
    assert binding is not None
    expected = build_configure_report(db_session, organization_id=ORG).to_dict()
    db_session.commit()
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    committed = False
    settings: list[tuple[str | None, str | None]] = []

    def change_between_reads(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal committed
        if committed or "FROM users" not in statement:
            return
        committed = True
        # The reader is paused after its users SELECT. A separate connection
        # commits both changes before the reader can select bindings.
        user.is_active = False
        binding.status = "suspended"
        db_session.commit()
        settings.append(
            (
                connection.scalar(
                    select(func.current_setting("transaction_isolation", type_=String))
                ),
                connection.scalar(
                    select(func.current_setting("transaction_read_only", type_=String))
                ),
            )
        )

    def engine_boundary(_url: str) -> Engine:
        return engine

    with monkeypatch.context() as patch:
        patch.setattr(configure_impact, "create_engine", engine_boundary)
        patch.setattr(engine, "dispose", lambda: None)
        event.listen(engine, "after_cursor_execute", change_between_reads)
        try:
            assert (
                configure_impact.main(
                    [
                        "--database-url",
                        "postgresql://",
                        "--organization",
                        ORG,
                        "--output-dir",
                        str(tmp_path / "snapshot"),
                    ]
                )
                == 0
            )
        finally:
            event.remove(engine, "after_cursor_execute", change_between_reads)
        assert committed
        first = _read_json(tmp_path / "snapshot" / "configure-exact-grants.json")
        assert first["principals"] == expected["principals"]
        assert first["existing_grants"] == expected["existing_grants"]
        assert settings == [("repeatable read", "on")]

        # A new assessment gets a new snapshot, not the previous tenant's state.
        assert (
            configure_impact.main(
                [
                    "--database-url",
                    "postgresql://",
                    "--organization",
                    ORG,
                    "--output-dir",
                    str(tmp_path / "fresh"),
                ]
            )
            == 0
        )
    fresh = build_configure_report(db_session, organization_id=ORG).to_dict()
    second = _read_json(tmp_path / "fresh" / "configure-exact-grants.json")
    assert second["principals"] == fresh["principals"] != first["principals"]
    assert second["existing_grants"] == fresh["existing_grants"] != first["existing_grants"]
    assert second["required_grants"] == []
    assert (
        _read_json(tmp_path / "snapshot" / "configure-after.json")["outcomes"]
        != _read_json(tmp_path / "fresh" / "configure-after.json")["outcomes"]
    )


def test_the_report_names_who_may_transmit_a_return(db_session: Session) -> None:
    """The filing cutover's gate: the `filing` column is the after-picture.

    Before 2026-09-20 the answer was "every scalar admin or approver", which is
    the `role` column beside it. After, it is exactly the holders of a
    Regulatory Reporting `submit` binding — and an Approver, however complete
    the grant, is not one of them.
    """
    db_session.add(Organization(id=ORG, name="Filing impact"))
    db_session.add(
        Bank(
            id=BANK,
            organization_id=ORG,
            name="Impact Bank",
            short_name="Impact",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    approver = _user(db_session, "checker@impact.example", role="approver")
    validator = _user(db_session, "filer@impact.example")
    db_session.commit()

    _grant(
        db_session,
        approver,
        RoleBundle.APPROVER,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    _grant(
        db_session,
        validator,
        RoleBundle.VALIDATOR,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    db_session.commit()

    rows = {row.email: row for row in build_report(db_session, organization_id=ORG)}
    assert rows["checker@impact.example"].filing_institutions == []
    assert rows["filer@impact.example"].filing_institutions == [BANK]
    assert BANK in render_table(list(rows.values()))


def test_the_report_names_who_may_read_examiner_evidence(db_session: Session) -> None:
    """The examiner cutover's gate (issue 180): who keeps the evidence bundle.

    The column is the whole-institution AUDIT/restricted `view` sentence, and
    only that sentence: a complete Liquidity grant, a confidential Audit grant
    and account administration all leave it empty, because none of them is the
    authority the bundle's entry gate asks for.
    """
    db_session.add(Organization(id=ORG, name="Examiner impact"))
    db_session.add(
        Bank(
            id=BANK,
            organization_id=ORG,
            name="Impact Bank",
            short_name="Impact",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    auditor = _user(db_session, "auditor@impact.example")
    confidential = _user(db_session, "confidential@impact.example")
    liquidity_only = _user(db_session, "liquidity@impact.example", role="analyst")
    account_admin = _user(db_session, "admin@impact.example", role="account_admin")
    db_session.commit()

    _grant(
        db_session,
        auditor,
        RoleBundle.AUDITOR,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.AUDIT,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    _grant(
        db_session,
        confidential,
        RoleBundle.AUDITOR,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.AUDIT,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    _grant(
        db_session,
        liquidity_only,
        RoleBundle.ANALYST,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    _grant(
        db_session,
        account_admin,
        RoleBundle.ACCOUNT_ADMIN,
        module_scope=ModuleScope.AUDIT,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    db_session.commit()

    rows = {row.email: row for row in build_report(db_session, organization_id=ORG)}
    assert rows["auditor@impact.example"].examiner_evidence == [BANK]
    assert rows["confidential@impact.example"].examiner_evidence == []
    assert rows["liquidity@impact.example"].examiner_evidence == []
    assert rows["admin@impact.example"].examiner_evidence == []
    cells = _rendered_cells(list(rows.values()))
    assert cells["auditor@impact.example"]["examiner"] == BANK
    assert cells["confidential@impact.example"]["examiner"] == "-"
    assert cells["liquidity@impact.example"]["examiner"] == "-"
    assert cells["admin@impact.example"]["examiner"] == "-"
