"""Per-example transactions for state machines whose services commit."""

from collections.abc import Callable
from typing import Protocol, cast
from uuid import UUID

from hypothesis import settings, stateful
from hypothesis.stateful import RuleBasedStateMachine
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from tests.support.helpers import ORG_1


def workflow_settings() -> settings:
    """Real calculations and freezes get a smaller budget than pure properties."""
    return settings(max_examples=12, stateful_step_count=20)


class _MachineRunner(Protocol):
    def __call__(
        self, factory: Callable[[], RuleBasedStateMachine], *, settings: settings
    ) -> None: ...


def run_database_machine(factory: Callable[[], RuleBasedStateMachine]) -> None:
    # Hypothesis does not annotate this runner's factory/settings arguments.
    runner = cast(
        _MachineRunner,
        stateful.run_state_machine_as_test,  # pyright: ignore[reportUnknownMemberType]
    )
    runner(factory, settings=workflow_settings())


class DatabaseMachine(RuleBasedStateMachine):
    def __init__(self, fixture: Session) -> None:
        super().__init__()
        fixture.commit()
        connection = fixture.get_bind()
        assert isinstance(connection, Connection)
        self.example = connection.begin_nested()
        self.db = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        self.db.info["organization_id"] = ORG_1

    def teardown(self) -> None:
        self.db.close()
        self.example.rollback()
        super().teardown()


def success_events(db: Session, entity_id: UUID, prefix: str) -> tuple[UUID, ...]:
    """Persisted workflow events, not the implementation's method invocations."""
    return tuple(
        db.scalars(
            select(AuditEvent.id)
            .where(AuditEvent.entity_id == str(entity_id), AuditEvent.event_type.startswith(prefix))
            .order_by(AuditEvent.id)
        )
    )
