"""A nullable JSON column must store SQL NULL, not the JSON value ``null``.

SQLAlchemy's ``JSON`` type writes Python ``None`` as the JSON value ``null``
unless ``none_as_null=True``. The two are not interchangeable in SQL: ``null``
is a present value, so ``col IS NULL`` is FALSE and ``col IS NOT NULL`` is TRUE
for a column that holds nothing.

That difference silently defeats a CHECK constraint written to prove a row
carries something. ``ck_icaap_ai_suggestions_validated_output`` says
``status <> 'validated' OR output IS NOT NULL``, so with the default a
``validated`` AI suggestion holding no draft at all satisfies the constraint
meant to guarantee it has one; and its sibling ``..._no_output`` fails in the
other direction, refusing a legitimate cancelled row. Both are governed rows
that are evidence of what was sent to a model.

This guard is generic on purpose. It finds every nullable JSON column on every
model and requires the flag, so the next model to add one cannot reintroduce the
defect — which is how this was found: the BI commentary table got the flag from
the start, and the ICAAP table it was modelled on did not have it.

This module's other-suite cases live in ``test_nullable_json_is_sql_null.py``.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.models import AuthorizationBinding
from tests.support.database import managed_engine
from tests.support.helpers import ORG_1, USER_1


def test_the_two_are_actually_different_in_sql(request: pytest.FixtureRequest) -> None:
    """The control. Without this, the census below could be asserting nothing."""

    class Probe(DeclarativeBase):
        pass

    class Default(Probe):
        __tablename__ = "probe_default"
        id: Mapped[int] = mapped_column(primary_key=True)
        payload: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    class Flagged(Probe):
        __tablename__ = "probe_flagged"
        id: Mapped[int] = mapped_column(primary_key=True)
        payload: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True), nullable=True)

    engine = managed_engine(request, "sqlite://")
    Probe.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Default(id=1, payload=None))
        session.add(Flagged(id=1, payload=None))
        session.commit()
        default_is_null = session.execute(
            sa.text("SELECT payload IS NULL FROM probe_default")
        ).scalar()
        flagged_is_null = session.execute(
            sa.text("SELECT payload IS NULL FROM probe_flagged")
        ).scalar()
    assert not default_is_null, "the default no longer writes JSON null; this guard is obsolete"
    assert flagged_is_null, "none_as_null no longer writes SQL NULL"


def test_a_whole_institution_binding_writes_sql_null_through_the_orm(
    db_session: Session,
) -> None:
    """The census above is static; this proves the WRITE, from SQL.

    Asserted with ``data_scope_values IS NULL`` evaluated by the database rather
    than by reading the attribute back: the Python round-trip is symmetrical and
    returns ``None`` either way, so it passes whichever value was stored and
    would have proved nothing. ``authorization_bindings`` is the one table where
    getting this wrong takes the whole product down rather than weakening one
    constraint — the CHECK refuses the row, and every baseline membership, every
    scoped grant and the Playwright bootstrap's first write are that shape.
    """

    binding = AuthorizationBinding(
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN.value,
        role_bundle=RoleBundle.VIEWER.value,
        institution_scope=InstitutionScope.ORGANIZATION.value,
        institution_id=None,
        module_scope=ModuleScope.ALL.value,
        sensitivity_scope=SensitivityScope.ALL.value,
        data_scope_kind=DataScope.ALL.value,
        data_scope_values=None,
        granted_by_type=GrantorType.SYSTEM.value,
        granted_by_id="nullable-json-guard",
        grant_reason="prove a whole-institution scope stores SQL NULL",
        granted_at=utc_now(),
        status=BindingStatus.ACTIVE.value,
        valid_from=utc_now(),
    )
    db_session.add(binding)
    db_session.flush()

    # Selected by the grantor marker rather than the id: a bound UUID compared
    # against the id column needs a dialect-specific literal form, and this test
    # has to give the same answer on SQLite and on Postgres.
    stored_is_null = db_session.execute(
        sa.text(
            "SELECT data_scope_values IS NULL FROM authorization_bindings "
            "WHERE granted_by_id = :marker"
        ),
        {"marker": "nullable-json-guard"},
    ).scalar()
    assert stored_is_null, (
        "the whole-institution binding stored the JSON value `null`, not SQL NULL. "
        "ck_authorization_bindings_data_scope_values requires IS NULL for kind "
        "'all', so this refuses every grant the product writes."
    )
