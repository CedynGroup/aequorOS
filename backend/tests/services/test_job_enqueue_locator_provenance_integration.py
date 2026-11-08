"""Every job producer persists its typed locator and enqueue provenance (#186, D9).

``enqueue`` stamps the locator dimensions and the enqueue provenance on every
row it inserts; a tenant-initiated enqueue without a locator is refused rather
than silently hidden. Coalescing never merges jobs whose locators differ, and
a coalesced successor keeps the ORIGINAL locator and provenance. Retries
(``fail_with_retry``) mutate the row in place, so they inherit everything.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.models import Job
from app.services import job_queue
from tests.support.helpers import ORG_1


def test_enqueue_stamps_locator_and_provenance(db_session: Session) -> None:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.context_provenance(
            TenantContext(organization_id=ORG_1, actor_user_id=uuid4()), action="data.refresh"
        ),
    )
    db_session.commit()
    assert job.institution_scope == InstitutionScope.INSTITUTION
    assert job.bank_id == "BK-TEST0001"
    assert job.module == Module.DATA
    assert job.sensitivity == Sensitivity.CONFIDENTIAL
    assert job.enqueued_by_principal is not None
    assert job.enqueued_by_principal.startswith("human:")
    assert job.enqueued_action == "data.refresh"
    assert job.enqueue_decision_reference is None


def test_an_org_scoped_locator_derives_the_organization_scope(db_session: Session) -> None:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "document_delete",
        locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.worker_provenance("worker.document_delete"),
    )
    db_session.commit()
    assert job.institution_scope == InstitutionScope.ORGANIZATION
    assert job.bank_id is None
    assert job.enqueued_by_principal == job_queue.WORKER_PRINCIPAL


def test_a_tenant_initiated_enqueue_without_a_locator_is_refused(db_session: Session) -> None:
    with pytest.raises(ValueError, match="locator"):
        job_queue.enqueue(
            db_session,
            ORG_1,
            "pipeline_refresh",
            provenance=job_queue.context_provenance(
                TenantContext(organization_id=ORG_1, actor_user_id=uuid4()), action="data.refresh"
            ),
        )


def test_a_worker_or_system_principal_may_carry_no_locator(db_session: Session) -> None:
    tick = job_queue.enqueue(
        db_session,
        ORG_1,
        "scheduled_tick",
        coalesce_key="tick:x",
        provenance=job_queue.worker_provenance("scheduler.seed_tick"),
    )
    db_session.commit()
    assert tick.module is None
    assert tick.sensitivity is None
    assert tick.institution_scope is None
    assert tick.enqueued_by_principal == job_queue.WORKER_PRINCIPAL
    assert tick.enqueued_action == "scheduler.seed_tick"


def test_an_unattributed_enqueue_persists_all_null_provenance(db_session: Session) -> None:
    """An enqueue without provenance behaves like a legacy row: hidden, still claimable."""
    job = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh")
    db_session.commit()
    assert job.enqueued_by_principal is None
    assert job.enqueued_action is None
    assert job.module is None
    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None and claimed.id == job.id


def test_a_machine_principal_is_recorded_as_machine(db_session: Session) -> None:
    key_id = uuid4()
    provenance = job_queue.context_provenance(
        TenantContext(
            organization_id=ORG_1,
            integration_key_id=key_id,
            integration_key_bank_id="BK-TEST0001",
        ),
        action="data.ingest",
    )
    assert provenance.principal == f"machine:{key_id}"


def test_an_impersonated_enqueue_carries_the_operator_principal(db_session: Session) -> None:
    provenance = job_queue.context_provenance(
        TenantContext(organization_id=ORG_1, actor_operator="inspector@example.com"),
        action="operator.fix",
    )
    assert provenance.principal == "operator:inspector@example.com"


def test_a_context_without_any_actor_falls_back_to_system(db_session: Session) -> None:
    provenance = job_queue.context_provenance(
        TenantContext(organization_id=ORG_1), action="system.op"
    )
    assert provenance.principal == job_queue.SYSTEM_PRINCIPAL


def test_coalescing_keeps_the_original_locator_and_provenance(db_session: Session) -> None:
    first = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        coalesce_key="refresh:x",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.context_provenance(
            TenantContext(organization_id=ORG_1, actor_user_id=uuid4()),
            action="data.refresh",
            decision_reference="evt-1",
        ),
    )
    db_session.commit()
    second = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        coalesce_key="refresh:x",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.system_provenance("live.reflow"),
    )
    db_session.commit()

    assert second.id == first.id
    assert second.enqueued_action == "data.refresh"
    assert second.enqueue_decision_reference == "evt-1"
    assert second.enqueued_by_principal == first.enqueued_by_principal


def test_coalescing_never_merges_differing_locators(db_session: Session) -> None:
    first = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.worker_provenance("worker.refresh"),
    )
    db_session.commit()
    # Same coalesce key, different bank: a different locator never merges (D9).
    other_bank = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0002",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.worker_provenance("worker.refresh"),
    )
    # Same bank, different module: still a different locator.
    other_module = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.worker_provenance("worker.refresh"),
    )
    # A located tenant job never merges into a locator-less (legacy or
    # worker-only) row either.
    unlocated = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        provenance=job_queue.system_provenance("system.op"),
    )
    db_session.commit()

    assert other_bank.id != first.id
    assert other_bank.bank_id == "BK-TEST0002"
    assert other_module.id != first.id
    assert other_module.module == Module.RISK
    assert unlocated.id != first.id
    assert unlocated.module is None
    ids = {row.id for row in db_session.scalars(select(Job))}
    assert len(ids) == 4


def test_locatorless_enqueues_still_coalesce_into_each_other(db_session: Session) -> None:
    """System jobs keep their existing coalescing (both locators NULL)."""
    first = job_queue.enqueue(
        db_session,
        ORG_1,
        "scheduled_tick",
        coalesce_key="tick:x",
        provenance=job_queue.worker_provenance("scheduler.seed_tick"),
    )
    second = job_queue.enqueue(
        db_session,
        ORG_1,
        "scheduled_tick",
        coalesce_key="tick:x",
        provenance=job_queue.worker_provenance("scheduler.seed_tick"),
    )
    db_session.commit()
    assert second.id == first.id


def test_retry_inherits_the_locator_and_provenance(db_session: Session) -> None:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id="BK-TEST0001",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.CONFIDENTIAL),
        provenance=job_queue.context_provenance(
            TenantContext(organization_id=ORG_1, actor_user_id=uuid4()), action="data.refresh"
        ),
    )
    db_session.commit()
    job_queue.fail_with_retry(db_session, job, "boom")
    db_session.commit()
    assert job.status == "queued"
    assert job.module == Module.DATA
    assert job.sensitivity == Sensitivity.CONFIDENTIAL
    assert job.institution_scope == InstitutionScope.INSTITUTION
    assert job.enqueued_action == "data.refresh"
    assert job.enqueued_by_principal is not None and job.enqueued_by_principal.startswith("human:")


def test_inherited_provenance_and_locator_copy_the_original_row(db_session: Session) -> None:
    original = job_queue.enqueue(
        db_session,
        ORG_1,
        "bi_mart_refresh",
        bank_id="BK-TEST0001",
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.AGGREGATED),
        provenance=job_queue.context_provenance(
            TenantContext(organization_id=ORG_1, actor_user_id=uuid4()),
            action="data.mart_refresh",
            decision_reference="evt-9",
        ),
    )
    db_session.commit()

    provenance = job_queue.inherited_provenance(original)
    locator = job_queue.inherited_locator(original)
    assert provenance.principal == original.enqueued_by_principal
    assert provenance.action == "data.mart_refresh"
    assert provenance.decision_reference == "evt-9"
    assert locator is not None and locator.module == Module.DATA
    assert locator.sensitivity == Sensitivity.AGGREGATED

    successor = job_queue.enqueue(
        db_session,
        ORG_1,
        "bi_mart_refresh",
        bank_id=original.bank_id,
        locator=locator,
        provenance=provenance,
    )
    db_session.commit()
    assert successor.enqueued_by_principal == original.enqueued_by_principal
    assert successor.enqueued_action == original.enqueued_action
    assert successor.module == original.module


def test_inherited_helpers_fall_back_to_worker_for_a_legacy_row(db_session: Session) -> None:
    legacy = Job(organization_id=ORG_1, job_type="pipeline_refresh", status="queued")
    db_session.add(legacy)
    db_session.commit()
    provenance = job_queue.inherited_provenance(legacy)
    assert provenance.principal == job_queue.WORKER_PRINCIPAL
    assert provenance.action == f"retry:{legacy.job_type}"
    assert job_queue.inherited_locator(legacy) is None
