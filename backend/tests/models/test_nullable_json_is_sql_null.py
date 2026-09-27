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
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db.base import Base


def test_the_two_are_actually_different_in_sql() -> None:
    """The control. Without this, the census below could be asserting nothing."""

    class Probe(sa.orm.DeclarativeBase):
        pass

    class Default(Probe):
        __tablename__ = "probe_default"
        id: sa.orm.Mapped[int] = sa.orm.mapped_column(primary_key=True)
        payload: sa.orm.Mapped[dict | None] = sa.orm.mapped_column(sa.JSON, nullable=True)

    class Flagged(Probe):
        __tablename__ = "probe_flagged"
        id: sa.orm.Mapped[int] = sa.orm.mapped_column(primary_key=True)
        payload: sa.orm.Mapped[dict | None] = sa.orm.mapped_column(
            sa.JSON(none_as_null=True), nullable=True
        )

    engine = sa.create_engine("sqlite://")
    Probe.metadata.create_all(engine)
    with sa.orm.Session(engine) as session:
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


def test_no_check_constraint_tests_a_json_column_that_cannot_be_null() -> None:
    """The precise defect class: a CHECK that asks ``IS NULL`` of a JSON column.

    Scoped deliberately. Many nullable JSON columns write the JSON value ``null``
    and nothing depends on the difference, so a census convicting all of them
    would be eighteen findings with no consequence and the first person to hit one
    would delete the test. What matters is a column whose nullness something
    ASSERTS: there the flag is the difference between a constraint that holds and
    a constraint that is satisfied by a row holding nothing.

    Found this way: the BI commentary table carried the flag from the start and the
    ICAAP AI table it was modelled on did not, while both are governed rows that
    record what was sent to a model and both constrain output against status.
    """
    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        json_columns = {
            column.name: column.type
            for column in table.columns
            if column.nullable and isinstance(column.type, sa.JSON)
        }
        if not json_columns:
            continue
        for constraint in table.constraints:
            if not isinstance(constraint, sa.CheckConstraint):
                continue
            text = str(constraint.sqltext)
            for name, kind in json_columns.items():
                tested = f"{name} IS NULL" in text or f"{name} IS NOT NULL" in text
                if tested and not getattr(kind, "none_as_null", False):
                    offenders.append(f"{table.name}.{name} (asserted by {constraint.name})")
    assert not offenders, (
        "a CHECK constraint tests these JSON columns for null, but they write the "
        "JSON value `null` rather than SQL NULL — so `IS NULL` is false and "
        "`IS NOT NULL` is true for a column holding nothing, and the constraint "
        "either passes for an empty row or refuses a legitimate one. Add "
        "`JSON(none_as_null=True)`: " + ", ".join(sorted(offenders))
    )


def test_the_scan_has_something_to_scan() -> None:
    """A guard over an empty subject reports clean forever."""
    constrained = [
        f"{table.name}.{constraint.name}"
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and any(
            f"{column.name} IS N" in str(constraint.sqltext)
            for column in table.columns
            if isinstance(column.type, sa.JSON)
        )
    ]
    assert constrained, (
        "no CHECK constraint tests a JSON column for null, so the guard above "
        "proves nothing. If that is now genuinely true, delete it rather than "
        "letting it pass vacuously."
    )
