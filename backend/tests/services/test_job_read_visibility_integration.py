"""Tenant job reads filter on the persisted locator and current authority (#186).

``GET /api/v1/jobs/{job_id}`` (detail) and ``GET /api/v1/jobs`` (paginated
list) are the tenant-facing queue reads, and they answer for ONE predicate: a
row is visible only when it carries a complete locator, its enqueue provenance
names a tenant principal, and one current ``view`` grant matches the locator —
anything else answers the same 404 (detail) or is absent with an unshifted
``total`` (list). Claim, retry and execution ignore the locator entirely, so
the worker keeps draining rows no tenant can poll (mission D9,
VAL-NOTIF-005/007/011).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.user import User
from app.identity.service import authorization
from app.models import Bank, Job
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from app.services import job_queue
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BASE = "/api/v1/jobs"
SAMPLE_BANK_ID = "BK-JOBV0001"
SIBLING_BANK_ID = "BK-JOBV0002"
COLLEAGUE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccc01")
_REQUEST_ID = re.compile(r'"request_id":"[^"]*"')


def _add_bank(db: Session, bank_id: str) -> None:
    """Create the bank a wrong-bank grant needs to validate against."""
    if db.get(Bank, bank_id) is None:
        db.add(
            Bank(
                id=bank_id,
                organization_id=ORG_1,
                name=f"Fixture {bank_id}",
                short_name="Fixture",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        db.commit()


def _opaque(response: Any) -> str:
    return _REQUEST_ID.sub('"request_id":""', response.text)


def _list(
    db_client: TestClient,
    version: int,
    user_id: UUID | None = None,
    **params: Any,
) -> dict[str, Any]:
    """GET the collection, asserting the route itself answers 200."""
    response = db_client.get(
        BASE,
        headers=headers(user_id=user_id, authorization_version=version),
        params=params,
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _human_provenance() -> job_queue.JobProvenance:
    return job_queue.JobProvenance(principal=f"human:{USER_1}", action="test.enqueue")


def _clear_bindings(db: Session) -> None:
    db.execute(delete(AuthorizationBinding))
    db.commit()


def _grant(  # noqa: PLR0913 - every binding dimension is an input to the match
    db: Session,
    *,
    user_id: UUID = USER_1,
    module: ModuleScope = ModuleScope.RISK,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    bank_id: str | None = None,
) -> int:
    if (
        db.scalars(
            select(User).where(User.id == user_id, User.organization_id == ORG_1)
        ).one_or_none()
        is None
    ):
        db.add(
            User(
                id=user_id,
                organization_id=ORG_1,
                email=f"{user_id.hex[:12]}@example.test",
                display_name="Job visibility colleague",
            )
        )
        db.commit()
    if bank_id is not None:
        _add_bank(db, bank_id)
    binding = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION if bank_id is None else InstitutionScope.INSTITUTION,
            bank_id,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "job-visibility-test"),
        reason="Exercise job read visibility",
        commit=False,
    )
    db.commit()
    assert binding is not None
    actor = db.scalars(
        select(User).where(User.id == user_id, User.organization_id == ORG_1)
    ).one()
    return actor.authorization_version


def _visible_job(db: Session, **locator: Any) -> Job:
    """Enqueue one visible-shaped row: complete locator, human provenance.

    ``bank_id`` rides through to ``enqueue`` (it is the locator's institution
    id there), the remaining two keys form the ``JobLocator``.
    """
    locator = {"module": Module.RISK, "sensitivity": Sensitivity.RESTRICTED, **locator}
    return job_queue.enqueue(
        db,
        ORG_1,
        "document_delete",
        bank_id=locator.pop("bank_id", None),
        locator=job_queue.JobLocator(**locator),
        provenance=_human_provenance(),
    )


@pytest.fixture
def unbound(db_session: Session) -> None:
    _clear_bindings(db_session)


@pytest.fixture
def job_id(db_session: Session) -> str:
    job = _visible_job(db_session)
    db_session.commit()
    return str(job.id)


def test_a_matching_current_grant_sees_the_job(
    db_client: TestClient, db_session: Session, unbound: None, job_id: str
) -> None:
    version = _grant(db_session)
    response = db_client.get(f"{BASE}/{job_id}", headers=headers(authorization_version=version))
    assert response.status_code == 200, response.text


@pytest.mark.usefixtures("unbound")
def test_the_demo_mode_veto_hides_jobs_with_a_matching_grant(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings  # noqa: PLC0415

    version = _grant(db_session)
    job = _visible_job(db_session)
    db_session.commit()
    monkeypatch.setattr(get_settings().app, "demo_mode", True)
    response = db_client.get(f"{BASE}/{job.id}", headers=headers(authorization_version=version))
    assert response.status_code == 404, _opaque(response)


@pytest.mark.parametrize(
    ("grant_kwargs", "locator_kwargs"),
    [
        # Wrong module: a CREDIT grant never admits a RISK-locator job.
        ({"module": ModuleScope.CREDIT}, {}),
        # Wrong sensitivity: confidential authority never admits restricted rows.
        ({"sensitivity": SensitivityScope.CONFIDENTIAL}, {}),
        # Wrong bank: an institution grant for the sample bank never admits
        # the sibling bank's row, though both belong to the same organization.
        ({"bank_id": SAMPLE_BANK_ID}, {"bank_id": SIBLING_BANK_ID}),
        # An institution grant never admits an organization-scoped row.
        ({"bank_id": SAMPLE_BANK_ID}, {}),
        # The row's own locator does not match the granted one.
        ({}, {"module": Module.CREDIT}),
        ({}, {"sensitivity": Sensitivity.CONFIDENTIAL}),
    ],
)
def test_a_non_matching_grant_hides_the_job(
    db_client: TestClient,
    db_session: Session,
    unbound: None,
    grant_kwargs: dict[str, Any],
    locator_kwargs: dict[str, Any],
) -> None:
    kwargs: dict[str, Any] = {
        "module": ModuleScope.RISK,
        "sensitivity": SensitivityScope.RESTRICTED,
        **locator_kwargs,
    }
    job = _visible_job(db_session, **kwargs)
    db_session.commit()
    # The no-extra-kwargs cases still hold the default matching grant, so the
    # row hides because ITS locator differs, not because the caller is unbound.
    version = _grant(db_session, **grant_kwargs)
    response = db_client.get(f"{BASE}/{job.id}", headers=headers(authorization_version=version))
    assert response.status_code == 404, response.text


def test_legacy_and_non_tenant_rows_stay_hidden(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    version = _grant(db_session)
    db_session.add(Job(organization_id=ORG_1, job_type="document_delete", status="completed"))
    for principal in ("worker", "system", "operator:inspector@example.test"):
        job_queue.enqueue(
            db_session,
            ORG_1,
            "document_delete",
            locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.RESTRICTED),
            provenance=job_queue.JobProvenance(principal=principal, action="test.enqueue"),
        )
    db_session.commit()
    for row in db_session.scalars(select(Job).where(Job.organization_id == ORG_1)):
        response = db_client.get(f"{BASE}/{row.id}", headers=headers(authorization_version=version))
        assert response.status_code == 404, f"{row.enqueued_by_principal}: {response.text}"


def test_hidden_is_the_unknown_object_answer(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    job = _visible_job(db_session)
    db_session.commit()
    # No grant holder: the row and a fabricated id answer identically.
    ungranted = headers(authorization_version=1)
    hidden = db_client.get(f"{BASE}/{job.id}", headers=ungranted)
    unknown = db_client.get(f"{BASE}/00000000-0000-7fff-8fff-ffffffffffff", headers=ungranted)
    assert hidden.status_code == unknown.status_code == 404
    assert _opaque(hidden) == _opaque(unknown)


def test_cross_tenant_job_never_visible(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    foreign = job_queue.enqueue(
        db_session,
        ORG_2,
        "document_delete",
        locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.RESTRICTED),
        provenance=job_queue.JobProvenance(principal=f"human:{USER_1}", action="test.enqueue"),
    )
    db_session.commit()
    # The foreign caller's own session is fresh (its user was never granted).
    response = db_client.get(f"{BASE}/{foreign.id}", headers=headers(ORG_2))
    assert response.status_code == 404, response.text


def test_revocation_removes_visibility(
    db_client: TestClient, db_session: Session, unbound: None, job_id: str
) -> None:
    version = _grant(db_session)
    allowed = db_client.get(f"{BASE}/{job_id}", headers=headers(authorization_version=version))
    assert allowed.status_code == 200, allowed.text
    for binding in db_session.scalars(
        select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1)
    ):
        binding.status = "revoked"
        binding.revoked_at = utc_now()
        binding.revoked_by_type = "system"
        binding.revoked_by_id = "job-visibility-test"
        binding.revoked_reason = "Revoke job visibility"
    new_version = authorization.invalidate_user_authorization(
        db_session, organization_id=ORG_1, user_id=USER_1, reason="Revoke job visibility"
    )
    db_session.commit()
    denied = db_client.get(
        f"{BASE}/{job_id}", headers=headers(authorization_version=new_version)
    )
    assert denied.status_code == 404, denied.text


def test_the_list_shows_exactly_what_the_detail_shows(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """One predicate for both reads: complete locator, tenant provenance, match.

    Every shape the detail read answers 404 for is absent from the list, and
    the total counts only the rows a page could actually hold.
    """
    version = _grant(db_session, bank_id=SAMPLE_BANK_ID)
    visible = _visible_job(db_session, bank_id=SAMPLE_BANK_ID)
    hidden = [
        # Wrong bank: same organization, an institution grant never crosses.
        _visible_job(db_session, bank_id=SIBLING_BANK_ID),
        # Wrong module and wrong sensitivity at the granted bank.
        _visible_job(db_session, bank_id=SAMPLE_BANK_ID, module=Module.CREDIT),
        _visible_job(
            db_session,
            bank_id=SAMPLE_BANK_ID,
            sensitivity=Sensitivity.CONFIDENTIAL,
        ),
    ]
    # Legacy locator-less row and the non-tenant provenance markers.
    db_session.add(Job(organization_id=ORG_1, job_type="document_delete", status="completed"))
    for principal in ("worker", "system", "operator:inspector@example.test"):
        job_queue.enqueue(
            db_session,
            ORG_1,
            "document_delete",
            locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.RESTRICTED),
            provenance=job_queue.JobProvenance(principal=principal, action="test.enqueue"),
        )
    # Another organization's row that would otherwise match the locator.
    job_queue.enqueue(
        db_session,
        ORG_2,
        "document_delete",
        bank_id=SAMPLE_BANK_ID,
        locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.RESTRICTED),
        provenance=job_queue.JobProvenance(principal=f"human:{USER_1}", action="test.enqueue"),
    )
    db_session.commit()
    payload = _list(db_client, version)
    assert [job["id"] for job in payload["jobs"]] == [str(visible.id)]
    assert payload["total"] == 1
    assert payload["has_more"] is False
    # List and detail agree row by row: each hidden id answers the detail 404.
    for row in hidden:
        detail = db_client.get(f"{BASE}/{row.id}", headers=headers(authorization_version=version))
        assert detail.status_code == 404, detail.text


def test_the_list_is_opaque_about_hidden_rows(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """A hidden row shifts nothing: same ids, same total, same page shape.

    If ``total`` or any page moved when a hidden row is added, the list would
    leak its existence — the aggregation form of the unknown-object answer.
    """
    version = _grant(db_session)
    for _ in range(3):
        _visible_job(db_session)
    db_session.commit()
    before = _list(db_client, version)
    # Every hidden shape arrives between the two reads.
    db_session.add(Job(organization_id=ORG_1, job_type="document_delete", status="completed"))
    job_queue.enqueue(
        db_session, ORG_1, "document_delete", provenance=job_queue.worker_provenance("worker.tick")
    )
    _visible_job(db_session, module=Module.CREDIT)
    job_queue.enqueue(
        db_session,
        ORG_2,
        "document_delete",
        locator=job_queue.JobLocator(module=Module.RISK, sensitivity=Sensitivity.RESTRICTED),
        provenance=job_queue.JobProvenance(principal=f"human:{USER_1}", action="test.enqueue"),
    )
    db_session.commit()
    assert _list(db_client, version) == before
    assert before["total"] == 3


def test_a_bank_targeted_job_serializes_its_platform_id(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """Bank-targeted producers persist ``BK-…`` ids; the reads echo them.

    ``Job.entity_id`` is a polymorphic string column: row-targeted producers
    store a UUID while bank-targeted ones (``bi_export``, re-encryption) store
    the institution's platform id. The response contract accepts whichever
    identifier the row carries — one ordinary authorized bank job must not
    turn the whole page (or its detail read) into a 500.
    """
    version = _grant(db_session)
    job = _visible_job(db_session)
    job.entity_type = "bank"
    job.entity_id = SAMPLE_BANK_ID
    db_session.commit()

    detail = db_client.get(f"{BASE}/{job.id}", headers=headers(authorization_version=version))
    assert detail.status_code == 200, detail.text
    assert detail.json()["entity_id"] == SAMPLE_BANK_ID
    payload = _list(db_client, version)
    assert payload["total"] == 1
    assert [row["entity_id"] for row in payload["jobs"]] == [SAMPLE_BANK_ID]


def test_pagination_sees_only_visible_rows(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """Paging walks the visible set exactly: exact total, no hidden drift."""
    version = _grant(db_session)
    created = [_visible_job(db_session) for _ in range(5)]
    # Interleave hidden rows so page offsets have to step over them.
    for _ in range(5):
        db_session.add(Job(organization_id=ORG_1, job_type="document_delete", status="completed"))
        job_queue.enqueue(
            db_session,
            ORG_1,
            "document_delete",
            provenance=job_queue.worker_provenance("worker.tick"),
        )
    db_session.commit()
    expected_ids = {str(job.id) for job in created}
    seen: list[str] = []
    offset = 0
    while True:
        payload = _list(db_client, version, limit=2, offset=offset)
        assert payload["total"] == 5
        assert payload["limit"] == 2 and payload["offset"] == offset
        seen.extend(job["id"] for job in payload["jobs"])
        if not payload["has_more"]:
            break
        offset += 2
    assert sorted(seen) == sorted(expected_ids)
    # Deterministic order: the same request answers the same page twice.
    assert _list(db_client, version, limit=2, offset=offset) == _list(
        db_client, version, limit=2, offset=offset
    )
    # Past the last page: an empty page, total unchanged, has_more False.
    tail = _list(db_client, version, limit=2, offset=10)
    assert tail["jobs"] == [] and tail["total"] == 5 and tail["has_more"] is False


@pytest.mark.usefixtures("unbound")
def test_a_caller_without_grants_sees_an_empty_page(
    db_client: TestClient, db_session: Session
) -> None:
    """Zero grants: an empty page with a zero total, never an error shape."""
    _visible_job(db_session)
    _visible_job(db_session)
    db_session.commit()
    payload = _list(db_client, 1)
    assert payload["jobs"] == [] and payload["total"] == 0 and payload["has_more"] is False


@pytest.mark.usefixtures("unbound")
def test_the_demo_mode_veto_empties_the_list(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request-wide veto the detail read honors empties the list too."""
    from app.core.config import get_settings  # noqa: PLC0415

    version = _grant(db_session)
    _visible_job(db_session)
    db_session.commit()
    monkeypatch.setattr(get_settings().app, "demo_mode", True)
    payload = _list(db_client, version)
    assert payload["jobs"] == [] and payload["total"] == 0


def test_revocation_empties_the_list(
    db_client: TestClient, db_session: Session, unbound: None, job_id: str
) -> None:
    version = _grant(db_session)
    assert _list(db_client, version)["total"] == 1
    for binding in db_session.scalars(
        select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1)
    ):
        binding.status = "revoked"
        binding.revoked_at = utc_now()
        binding.revoked_by_type = "system"
        binding.revoked_by_id = "job-visibility-test"
        binding.revoked_reason = "Revoke job visibility"
    new_version = authorization.invalidate_user_authorization(
        db_session, organization_id=ORG_1, user_id=USER_1, reason="Revoke job visibility"
    )
    db_session.commit()
    payload = _list(db_client, new_version)
    assert payload["jobs"] == [] and payload["total"] == 0


def test_the_worker_still_claims_and_completes_rows(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """Scoping changes visibility only: claim and completion are untouched."""
    version = _grant(db_session)
    job = _visible_job(db_session)
    hidden = job_queue.enqueue(
        db_session,
        ORG_1,
        "document_delete",
        provenance=job_queue.worker_provenance("worker.tick"),
    )
    db_session.commit()
    for expected in (job, hidden):
        claimed = job_queue.claim_next(db_session, utc_now(), ("document_delete",))
        assert claimed is not None and claimed.id == expected.id
        done = job_queue.complete(db_session, claimed, progress={"done": True})
        assert done.status == "succeeded"
    # The visible row still polls; the worker-only row never does — in the
    # list as well, even after the worker has processed it.
    fresh = headers(authorization_version=version)
    assert db_client.get(f"{BASE}/{job.id}", headers=fresh).status_code == 200
    assert db_client.get(f"{BASE}/{hidden.id}", headers=fresh).status_code == 404
    payload = _list(db_client, version)
    assert [row["id"] for row in payload["jobs"]] == [str(job.id)]
    assert payload["total"] == 1


def _private_bi_question(db: Session) -> Job:
    """A private BI question queued by USER_1 under its DATA/aggregated locator."""
    return job_queue.enqueue(
        db,
        ORG_1,
        "bi_nlq_translate",
        bank_id=SAMPLE_BANK_ID,
        payload={"principal_user_id": str(USER_1)},
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.AGGREGATED),
        provenance=_human_provenance(),
    )


def _private_bi_export(db: Session) -> Job:
    """A governed export queued by USER_1 under its DATA/aggregated locator."""
    return job_queue.enqueue(
        db,
        ORG_1,
        "bi_export",
        bank_id=SAMPLE_BANK_ID,
        payload={
            "principal_user_id": str(USER_1),
            "format": "csv",
            "export_class": "record_level",
        },
        locator=job_queue.JobLocator(module=Module.DATA, sensitivity=Sensitivity.AGGREGATED),
        provenance=_human_provenance(),
    )


def test_a_private_bi_export_answers_only_to_its_requester(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """A colleague holding the same authority can neither list nor read it.

    An export's download link is a bearer credential, so the status route
    answers 404 to anyone but the principal who asked for it — and the
    existence of another person's export (its object path, filename, row
    count, size) is not this caller's business either. The queue surface
    (list and detail alike, before pagination) refuses a colleague exactly
    the way it refuses them for a private question.
    """
    job = _private_bi_export(db_session)
    db_session.commit()
    requester_version = _grant(
        db_session,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.AGGREGATED,
        bank_id=SAMPLE_BANK_ID,
    )
    colleague_version = _grant(
        db_session,
        user_id=COLLEAGUE_ID,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.AGGREGATED,
        bank_id=SAMPLE_BANK_ID,
    )

    mine = _list(db_client, requester_version, user_id=USER_1)
    assert [row["id"] for row in mine["jobs"]] == [str(job.id)]
    assert mine["total"] == 1

    theirs = _list(db_client, colleague_version, user_id=COLLEAGUE_ID)
    assert theirs["jobs"] == []
    assert theirs["total"] == 0

    hidden = db_client.get(
        f"{BASE}/{job.id}",
        headers=headers(user_id=COLLEAGUE_ID, authorization_version=colleague_version),
    )
    assert hidden.status_code == 404
    detail = db_client.get(
        f"{BASE}/{job.id}", headers=headers(authorization_version=requester_version)
    )
    assert detail.status_code == 200


def test_a_private_bi_question_answers_only_to_its_requester(
    db_client: TestClient, db_session: Session, unbound: None
) -> None:
    """A colleague holding the same authority can neither list nor read it.

    A question is one reader's, translated over the members THAT reader may
    see, so the dedicated question routes already refuse a colleague — the
    queue surface (list and detail alike, before pagination) refuses them
    too: the existence of another person's question is not this caller's
    business (the bi_export rule).
    """
    job = _private_bi_question(db_session)
    db_session.commit()
    requester_version = _grant(
        db_session,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.AGGREGATED,
        bank_id=SAMPLE_BANK_ID,
    )
    colleague_version = _grant(
        db_session,
        user_id=COLLEAGUE_ID,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.AGGREGATED,
        bank_id=SAMPLE_BANK_ID,
    )

    mine = _list(db_client, requester_version, user_id=USER_1)
    assert [row["id"] for row in mine["jobs"]] == [str(job.id)]
    assert mine["total"] == 1

    theirs = _list(db_client, colleague_version, user_id=COLLEAGUE_ID)
    assert theirs["jobs"] == []
    assert theirs["total"] == 0

    hidden = db_client.get(
        f"{BASE}/{job.id}",
        headers=headers(user_id=COLLEAGUE_ID, authorization_version=colleague_version),
    )
    assert hidden.status_code == 404
    detail = db_client.get(
        f"{BASE}/{job.id}", headers=headers(authorization_version=requester_version)
    )
    assert detail.status_code == 200
