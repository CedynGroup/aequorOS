"""The seed's timeline: what happens, when, through which service."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.service import AccessRequestIn, access_view, approve, deny, request_access
from app.audit.chain import append
from app.core.clock import add_business_days
from app.db.models import ApiClient, ApiDailyStat, Institution, Query, Submission
from app.enrolment.service import (
    RegisterKeyRequest,
    StatusRequest,
    register_signing_key,
    retire_signing_key,
    revoke_client,
    set_status,
)
from app.filing.obligations import grant_extension, reporting_dates
from app.jobs import verify_audit
from app.portal.institutions import add_note
from app.portal.submissions import submission_detail
from app.queries.service import (
    OpenQueryIn,
    ResponseIn,
    StaffMessageIn,
    acknowledge,
    close_query,
    respond,
    staff_message,
    staff_open_query,
)
from app.returns.catalogue_loader import draft_of
from app.returns.service import DraftIn, create_draft
from app.returns.service import approve as approve_version
from app.seed.catalogue_payloads import reclassify
from app.seed.client import InstitutionKeys
from app.seed.payloads import rng_for
from app.seed.registry import bootstrap_registry
from app.seed.world import (
    DEFAULT_START,
    EXTENSIONS,
    GENERATED_NAMES,
    INBOX,
    MONTHLY,
    NAMED,
    NORTHGATE_HISTORY,
    OCT,
    OCT_CODES,
    OCT_EXTRA,
    OCT_PATTERNS,
    PIPELINE,
    QUERY_TEMPLATES,
    SEED_START,
    TODAY,
    Inst,
    World,
    at,
    corrupt,
    definition_doc,
)

HISTORY_END = date(2026, 9, 30)


def applies(inst: Inst, code: str, day: date) -> bool:
    """Whether ``inst`` files ``code`` for ``day``: its licence type (the aligned
    definition's ``applies_to``), the return's start in the world's timeline, and the
    institution being live."""
    doc = definition_doc(code)
    return (
        inst.type in doc.applies_to
        and day >= max(SEED_START.get(code, DEFAULT_START), doc.first_reporting_date)
        and (inst.live_since is None or day >= inst.live_since)
    )


def can_reject(code: str) -> bool:
    """Whether the definition has a rule that inconsistent figures would break."""
    return bool(definition_doc(code).rules)


def generated_institutions() -> list[Inst]:
    out: list[Inst] = []
    for index, (name, type_) in enumerate(GENERATED_NAMES):
        rng = rng_for("generated", name)
        code = f"{301 + index * 4:04d}"
        api = type_ == "universal_bank" or rng.random() < 0.55
        software = ("AequorOS" if rng.random() < 0.6 else "In-house system") if api else None
        slug = name.split()[0][:3].lower()
        not_after = date(2027, 1 + rng.randint(0, 10), 1 + rng.randint(0, 27))
        if name in ("Amberline Bank", "Copperleaf Bank"):
            not_after = date(2026, 12, 8) if name == "Amberline Bank" else date(2026, 12, 15)
        base = {
            "universal_bank": 1_200_000 + rng.randint(0, 2_400_000),
            "rural_bank": 60_000 + rng.randint(0, 220_000),
            "savings_and_loans": 120_000 + rng.randint(0, 600_000),
        }[type_]
        out.append(
            Inst(
                code,
                name,
                type_,
                "api" if api else "portal_upload",
                software,
                "aboateng" if index % 2 == 0 else "kowusu",
                live_since=date(2026, 5, 15),
                client_id=f"{slug}-prod-01" if api else None,
                not_after=not_after if api else None,
                client_issued=date(2026, 4, 25) if api else None,
                portal_users=1 + rng.randint(0, 4),
                base=base,
                contacts=[
                    ("Compliance officer", "Compliance Desk"),
                    ("Technical contact", "Reporting Systems"),
                ],
            )
        )
    return out


def plan(world: World) -> None:
    ctx = world.ctx
    institutions = [*NAMED, *generated_institutions(), *PIPELINE]
    for inst in institutions:
        world.institutions[inst.code] = inst

    # 1. Registry: synthetic definitions drafted and approved through the workflow.
    def _registry(session: Session) -> None:
        bootstrap_registry(session, ctx, at(date(2025, 10, 1), 9))

    world.schedule(at(date(2025, 10, 1), 9), "registry", _registry)

    # 2. Enrolment through the operator service layer.
    for index, inst in enumerate(institutions):
        named = inst in NAMED
        if named:
            enrolled = at(date(2025, 11, 1) + timedelta(days=index % 5), 9 + index % 8)
        elif inst.live_since is not None:
            enrolled = at(date(2026, 4, 20), 9) + timedelta(minutes=index * 11)
        elif inst.client_issued is not None:
            enrolled = at(inst.client_issued - timedelta(days=5), 10)
        else:
            enrolled = at(date(2026, 11, 9) + timedelta(days=index % 5), 11)

        def _enrol(session: Session, inst: Inst = inst, when: datetime = enrolled) -> None:
            world.enrol(session, inst, when)
            if inst.code == "0117":
                inst.keys = InstitutionKeys(
                    client_kid="pgb-client-1", signing_kid="pgb-sign-2025-11"
                )
            elif inst.signing_registered is None:
                prefix = inst.keys.signing_kid.rsplit("-sign-", 1)[0]
                inst.keys.signing_kid = (
                    f"{prefix}-sign-2025-11" if inst in NAMED else f"{prefix}-sign-2026-04"
                )

        world.schedule(enrolled, f"enrol {inst.code}", _enrol)
        if inst.client_id and inst.client_issued and inst.not_after:
            issued_at = at(inst.client_issued, 10)

            def _issue(session: Session, inst: Inst = inst) -> None:
                assert inst.client_id
                assert inst.client_issued
                assert inst.not_after
                world.issue(session, inst, inst.client_id, inst.client_issued, inst.not_after)

            world.schedule(issued_at, f"client {inst.client_id}", _issue)
        if inst.stage != "requested" or inst.client_id:
            key_at = at(inst.client_issued or (enrolled + timedelta(days=1)).date(), 11)
            world.schedule(
                key_at,
                f"signing key {inst.code}",
                lambda s, inst=inst, when=key_at: world.register_key(s, inst, when),
            )
        if inst.live_since is not None:
            live_at = at(inst.live_since, 9)
            world.schedule(
                live_at,
                f"live {inst.code}",
                lambda s, inst=inst, when=live_at: set_status(
                    s,
                    ctx,
                    world.operator,
                    inst.code,
                    StatusRequest(status="live", reason="Enrolment complete"),
                    at=when,
                ),
            )

    # Palmgrove rotates its signing key on 2 Feb 2026 (filings signed with the old key stay verifiable).
    def _rotate_palmgrove(session: Session) -> None:
        inst = world.institutions["0117"]
        new = InstitutionKeys(
            client_key=inst.keys.client_key,
            client_kid=inst.keys.client_kid,
            signing_kid="pgb-sign-2026-01",
        )
        register_signing_key(
            session,
            ctx,
            world.operator,
            inst.code,
            RegisterKeyRequest(kid=new.signing_kid, public_jwk=new.signing_jwk()),
        )
        retire_signing_key(session, ctx, world.operator, inst.code, inst.keys.signing_kid)
        inst.keys = new

    world.schedule(at(date(2026, 2, 2), 10, 30), "rotate palmgrove key", _rotate_palmgrove)

    # Ridgeway rotates its client certificate on 2 Sep 2026.
    def _rotate_ridgeway(session: Session) -> None:
        inst = world.institutions["0155"]
        world.issue(session, inst, "rcb-prod-02", date(2026, 9, 2), date(2027, 9, 2))
        revoke_client(session, ctx, world.operator, "rcb-prod-01", "Replaced by rcb-prod-02")
        inst.client_id = "rcb-prod-02"

    world.schedule(at(date(2026, 9, 2), 10, 5), "rotate ridgeway client", _rotate_ridgeway)

    plan_history(world)
    plan_october(world)
    plan_periodic(world)
    plan_design_events(world)
    plan_access(world)


def _history_state(world: World, inst: Inst, code: str, day: date) -> str:
    if inst.code == "0231" and code in NORTHGATE_HISTORY and day >= date(2025, 12, 31):
        months = (day.year - 2025) * 12 + day.month - 12
        return NORTHGATE_HISTORY[code][months]
    if inst.code == "0171" and code == "BSD13" and day == date(2026, 9, 30):
        return "L"
    rng = rng_for("history", inst.code, code, day.isoformat())
    return "L" if rng.random() < 0.05 else "O"


def plan_history(world: World) -> None:
    rejected_budget = 8
    query_budget = 56
    queried = 0
    for inst in world.institutions.values():
        if inst.live_since is None:
            continue
        for day in reporting_dates("monthly", inst.live_since, HISTORY_END):
            for order, code in enumerate(MONTHLY):
                if not applies(inst, code, day):
                    continue
                state = _history_state(world, inst, code, day)
                if state == "X":
                    continue
                when = (
                    world.on_time(inst, code, day, order)
                    if state == "O"
                    else world.late(inst, code, day)
                )
                rng = rng_for("hist-extra", inst.code, code, day.isoformat())
                if (
                    rejected_budget
                    and day.year == 2026
                    and code not in ("BSD2", "BSD6", "BSD5A")
                    and can_reject(code)
                    and rng.random() < 0.01
                ):
                    rejected_budget -= 1
                    plan_rejected_then_amended(world, inst, code, day, when)
                    continue
                world.plan_file(when, inst, code, day)
                accept_at = world.accept_after(when, inst, f"{code}{day}")
                if (
                    query_budget
                    and day.year == 2026
                    and rng.random() < 0.025
                    and accept_at < datetime(2026, 11, 10, tzinfo=when.tzinfo)
                ):
                    query_budget -= 1
                    queried += 1
                    plan_history_query(world, inst, code, day, when, queried)
                    continue
                world.plan_accept(accept_at, inst, code, day, inst.supervisor or "kowusu")


def plan_rejected_then_amended(
    world: World, inst: Inst, code: str, day: date, when: datetime
) -> None:
    world.count_query(when)
    world.plan_file(when, inst, code, day, payload=corrupt(code, world.payload(inst, code, day)))
    amend_at = when + timedelta(days=1, hours=2)

    def _amend(session: Session) -> None:
        current = world.receipts[(inst.code, code, day)][-1]
        sub = world.file(
            session,
            inst,
            code,
            day,
            supersedes=current,
            reason="Corrected the totals that failed validation.",
        )
        query = session.scalars(select(Query).where(Query.receipt_id == current)).first()
        if query is not None:
            respond(
                session,
                ctx_of(world),
                world.rows[inst.code],
                world.filer(inst).actor,
                query.number,
                ResponseIn(
                    body="Corrected return filed as an amendment.",
                    resubmission_receipt_id=sub.receipt_id,
                ),
                author_role="Compliance Officer" if inst.channel == "portal_upload" else None,
            )

    if amend_at.year == 2026:
        world.filings_2026 += 1
    world.schedule(amend_at, f"amend {inst.code} {code} {day}", _amend)

    def _close(session: Session) -> None:
        current = world.receipts[(inst.code, code, day)][0]
        query = session.scalars(select(Query).where(Query.receipt_id == current)).first()
        if query is not None and query.status != "closed":
            close_query(
                session,
                ctx_of(world),
                world.staff(session, inst.supervisor or "kowusu"),
                query.number,
            )

    world.schedule(amend_at + timedelta(hours=4), f"close auto query {inst.code}", _close)
    world.plan_accept(
        amend_at + timedelta(days=1, hours=1), inst, code, day, inst.supervisor or "kowusu"
    )


def ctx_of(world: World):
    return world.ctx


def plan_history_query(
    world: World, inst: Inst, code: str, day: date, filed: datetime, index: int
) -> None:
    subject, body, answer = QUERY_TEMPLATES[index % len(QUERY_TEMPLATES)]
    opened = filed + timedelta(days=1, hours=1)
    supervisor = inst.supervisor or "kowusu"
    world.count_query(opened)

    def _open(session: Session) -> None:
        receipt = world.receipts[(inst.code, code, day)][-1]
        staff_open_query(
            session,
            world.ctx,
            world.staff(session, supervisor),
            OpenQueryIn(
                receipt_id=receipt,
                subject=f"{subject} in {code}",
                body=body,
                kind="query",
                due_at=opened + timedelta(days=5),
            ),
        )

    world.schedule(opened, f"history query {inst.code} {code}", _open)

    def _reply(session: Session) -> None:
        receipt = world.receipts[(inst.code, code, day)][-1]
        query = session.scalars(select(Query).where(Query.receipt_id == receipt)).one()
        respond(
            session,
            world.ctx,
            world.rows[inst.code],
            world.filer(inst).actor,
            query.number,
            ResponseIn(body=answer, resubmission_receipt_id=None),
            author_role="Compliance Officer" if inst.channel == "portal_upload" else None,
        )

    world.schedule(opened + timedelta(days=2), "history reply", _reply)

    def _close(session: Session) -> None:
        receipt = world.receipts[(inst.code, code, day)][-1]
        query = session.scalars(select(Query).where(Query.receipt_id == receipt)).one()
        close_query(session, world.ctx, world.staff(session, supervisor), query.number)

    world.schedule(opened + timedelta(days=3), "history close", _close)
    world.plan_accept(opened + timedelta(days=3, minutes=20), inst, code, day, supervisor)


def plan_october(world: World) -> None:
    for inst in world.institutions.values():
        if inst.live_since is None:
            continue
        named = inst.code in OCT_PATTERNS
        for order, code in enumerate(MONTHLY):
            if not applies(inst, code, OCT):
                continue
            key = (inst.code, code)
            if key in INBOX:
                continue  # the design's own rows, planned in plan_design_events
            if named:
                pattern = OCT_PATTERNS[inst.code]
                state = (
                    pattern[OCT_CODES.index(code)]
                    if code in OCT_CODES
                    else OCT_EXTRA.get((inst.code, code), "O")
                )
            else:
                state = "L" if rng_for("oct-late", inst.code, code).random() < 0.035 else "O"
            if state in ("X", "N"):
                continue
            if state == "O":
                when = world.on_time(inst, code, OCT, order)
            else:
                when = world.late(
                    inst, code, OCT, latest=datetime(2026, 11, 19, 18, tzinfo=TODAY.tzinfo)
                )
            world.plan_file(when, inst, code, OCT)
            rng = rng_for("oct-accept", inst.code, code)
            if (inst.code, code) == ("0163", "BSD13"):
                continue  # Kestrel's BSD13 is queried on 18 Nov
            if rng.random() < 0.9:
                accept_at = min(
                    world.accept_after(when, inst, f"oct{code}"),
                    datetime(2026, 11, 20, 9, 30, tzinfo=TODAY.tzinfo),
                )
                if accept_at > when + timedelta(minutes=5):
                    world.plan_accept(accept_at, inst, code, OCT, inst.supervisor or "kowusu")


def plan_periodic(world: World) -> None:
    live = [i for i in world.institutions.values() if i.live_since is not None]
    # Weekly BSD1 for 30 Oct, 6 Nov and 13 Nov.
    late_quota = {date(2026, 11, 6): 2, date(2026, 11, 13): 7}
    for day in reporting_dates("weekly", date(2026, 10, 30), date(2026, 11, 13)):
        late_left = late_quota.get(day, 0)
        for index, inst in enumerate(live):
            if not applies(inst, "BSD1", day):
                continue
            due = world.due("BSD1", day)
            if late_left and index % 7 == 3:
                late_left -= 1
                when = at(due + timedelta(days=1 + index % 3), 10, index % 60)
            else:
                when = at(due, 8, 0) + timedelta(minutes=index * 3)
            world.plan_file(when, inst, "BSD1", day)
            world.plan_accept(
                when + timedelta(hours=5), inst, "BSD1", day, inst.supervisor or "kowusu"
            )
    # Daily DBK-DAILY for 2–19 Nov, filed the next business day before 09:30 (auto-accepted).
    for day in reporting_dates("daily", date(2026, 11, 2), date(2026, 11, 19)):
        filed_day = add_business_days(day, 1)
        for index, inst in enumerate(live):
            if applies(inst, "DBK-DAILY", day):
                world.plan_file(
                    at(filed_day, 6, 30) + timedelta(minutes=index * 3, seconds=index),
                    inst,
                    "DBK-DAILY",
                    day,
                )
    # Quarterly IRRBB-PILOT for 30 Jun (all pilot banks) and 30 Sep (half so far).
    for index, inst in enumerate(live):
        for day in (date(2026, 6, 30), date(2026, 9, 30)):
            if not applies(inst, "IRRBB-PILOT", day):
                continue
            if day.month == 9 and index % 2:
                continue
            when = (
                at(world.due("IRRBB-PILOT", day) - timedelta(days=5 + index % 5), 11)
                if day.month == 6
                else at(date(2026, 11, 3) + timedelta(days=index % 12), 11)
            )
            world.plan_file(when, inst, "IRRBB-PILOT", day)
            world.plan_accept(
                when + timedelta(days=1), inst, "IRRBB-PILOT", day, inst.supervisor or "kowusu"
            )


def plan_design_events(world: World) -> None:
    ctx = world.ctx
    named = {i.code: i for i in NAMED}

    for (code_, return_code), (when, behaviour) in INBOX.items():
        inst = named[code_]
        if behaviour == "reject":
            world.count_query(when)
            world.plan_file(
                when,
                inst,
                return_code,
                OCT,
                payload=corrupt(return_code, world.payload(inst, return_code, OCT)),
            )
        elif behaviour == "validating":
            world.plan_file(when, inst, return_code, OCT, validate=False)
        elif behaviour == "palmgrove":
            world.plan_file(when, inst, return_code, OCT)
        else:
            world.plan_file(when, inst, return_code, OCT)
        if behaviour.startswith("accept:"):
            parts = behaviour.split(":")
            subject = parts[1]
            if len(parts) == 5:
                accept_at = when.replace(
                    hour=int(parts[2]), minute=int(parts[3]), second=int(parts[4])
                )
            else:
                accept_at = when + timedelta(minutes=41, seconds=13)
            world.plan_accept(accept_at, inst, return_code, OCT, subject)

    # Goldfield's daily return for 20 Nov, auto-accepted at 08:47.
    world.plan_file(
        at(date(2026, 11, 21), 8, 47, 30), named["0149"], "DBK-DAILY", date(2026, 11, 20)
    )

    # Palmgrove amends BSD2 (version 2) and A. Boateng opens it.
    palmgrove = named["0117"]

    def _palmgrove_v2(session: Session) -> None:
        current = world.receipts[("0117", "BSD2", OCT)][-1]
        amended, _moved = reclassify(
            definition_doc("BSD2"), world.payload(palmgrove, "BSD2", OCT), Decimal("4.2")
        )
        world.file(
            session,
            palmgrove,
            "BSD2",
            OCT,
            payload=amended,
            supersedes=current,
            reason="Reclassified GHS 4.2m between two lines of the same total after "
            "year-to-date review.",
        )

    world.filings_2026 += 1
    world.schedule(at(date(2026, 11, 21), 15, 57, 12, 418), "palmgrove v2", _palmgrove_v2)
    world.schedule(
        at(date(2026, 11, 21), 16, 10, 22),
        "boateng opens palmgrove",
        lambda s: submission_detail(
            s, ctx, world.staff(s, "aboateng"), world.receipts[("0117", "BSD2", OCT)][-1]
        ),
    )

    # Northgate: a resubmission request yesterday, the institution's reply today.
    northgate = named["0231"]
    world.count_query(at(date(2026, 11, 20), 14, 31))
    world.schedule(
        at(date(2026, 11, 20), 14, 31),
        "northgate resubmission",
        lambda s: staff_open_query(
            s,
            ctx,
            world.staff(s, "aboateng"),
            OpenQueryIn(
                receipt_id=world.receipts[("0231", "SDI-LMT-MONTHLY", OCT)][-1],
                kind="resubmission",
                subject="Liquidity return filed without its maturity ladder",
                body="Your liquidity return for 31 Oct 2026 has no maturity ladder, and your large "
                "exposures return for the same date has not been filed. Please file both, amending "
                "the liquidity return with the ladder.",
                due_at=at(date(2026, 11, 25), 17),
            ),
        ),
    )
    world.schedule(
        at(date(2026, 11, 21), 10, 20, 5),
        "northgate reply",
        lambda s: respond(
            s,
            ctx,
            world.rows["0231"],
            world.filer(northgate).actor,
            _query_for(s, world.receipts[("0231", "SDI-LMT-MONTHLY", OCT)][-1]),
            ResponseIn(
                body="Our core banking migration delayed the ladder and the large exposures return. We "
                "are refiling both today.",
                resubmission_receipt_id=None,
            ),
            author_role="Compliance Officer",
        ),
    )

    # Riverbend: K. Owusu's query on its NPL return (QRY-2026-0318).
    riverbend = named["0312"]
    world.schedule(
        at(date(2026, 11, 21), 9, 2, 17), "owusu signs in", lambda s: world.staff(s, "kowusu")
    )
    world.schedule(
        world.riverbend_query_at,
        "riverbend query",
        lambda s: staff_open_query(
            s,
            ctx,
            world.staff(s, "kowusu"),
            OpenQueryIn(
                receipt_id=world.receipts[("0312", "NPL-MONTHLY", OCT)][-1],
                kind="query",
                subject="Basis for two upgraded facilities in the NPL return",
                body="Two facilities classified Substandard in September are reported as Current in this return: "
                "references RB-LN-20417 and RB-LN-20983, together GHS 1.9m.\n\nPlease provide the basis for "
                "each upgrade, the repayment record since 30 Sep 2026 and the credit committee minute that "
                "approved the change.",
                due_at=at(date(2026, 11, 24), 17),
            ),
        ),
    )
    world.schedule(
        at(date(2026, 11, 21), 11, 31, 6),
        "riverbend acknowledges",
        lambda s: acknowledge(
            s,
            ctx,
            world.rows["0312"].id,
            "0312",
            world.filer(riverbend).actor,
            _query_for(s, world.receipts[("0312", "NPL-MONTHLY", OCT)][-1]),
        ),
    )
    world.schedule(
        at(date(2026, 11, 21), 15, 2, 40),
        "riverbend reply",
        lambda s: respond(
            s,
            ctx,
            world.rows["0312"],
            world.filer(riverbend).actor,
            _query_for(s, world.receipts[("0312", "NPL-MONTHLY", OCT)][-1]),
            ResponseIn(
                body="Acknowledged. The credit committee minute and repayment schedules are being "
                "collated; we will respond in full before the deadline.",
                resubmission_receipt_id=None,
            ),
            author_role="Compliance Officer",
        ),
    )
    world.schedule(
        at(date(2026, 11, 21), 15, 10, 12),
        "owusu follow-up",
        lambda s: staff_message(
            s,
            ctx,
            world.staff(s, "kowusu"),
            _query_for(s, world.receipts[("0312", "NPL-MONTHLY", OCT)][-1]),
            StaffMessageIn(
                body="Thank you. We will review the full response when it arrives, by 24 Nov 2026.",
                require_resubmission=False,
                due_at=None,
            ),
        ),
    )

    # Other open queries from the design.
    def _open(
        when: datetime,
        inst_code: str,
        return_code: str,
        day: date,
        subject: str,
        body: str,
        due: datetime,
        supervisor: str,
    ) -> None:
        world.count_query(when)
        world.schedule(
            when,
            f"query {inst_code} {return_code}",
            lambda s: staff_open_query(
                s,
                ctx,
                world.staff(s, supervisor),
                OpenQueryIn(
                    receipt_id=world.receipts[(inst_code, return_code, day)][-1],
                    kind="query",
                    subject=subject,
                    body=body,
                    due_at=due,
                ),
            ),
        )

    sep = date(2026, 9, 30)
    _open(
        at(date(2026, 11, 15), 10, 4),
        "0171",
        "BSD13",
        sep,
        "Late BSD13: confirm controls remediation",
        "Your BSD13 for 30 Sep 2026 was filed late. Please confirm the remediation of the controls that "
        "delayed it.",
        at(date(2026, 11, 28), 17),
        "aboateng",
    )
    _open(
        at(date(2026, 11, 17), 9, 40),
        "0184",
        "NPL-MONTHLY",
        sep,
        "NPL ratio movement in September",
        "Please explain the movement in the non-performing loan ratio reported for September.",
        at(date(2026, 11, 26), 17),
        "aboateng",
    )
    _open(
        at(date(2026, 11, 17), 11, 5),
        "0192",
        "LE-MONTHLY",
        sep,
        "Large exposure to one connected group",
        "Please set out the components of the exposure to connected group 1 and the approvals for it.",
        at(date(2026, 11, 24), 17),
        "aboateng",
    )
    world.schedule(
        at(date(2026, 11, 19), 15, 20),
        "summit reply",
        lambda s: respond(
            s,
            ctx,
            world.rows["0192"],
            world.filer(named["0192"]).actor,
            _query_for(s, world.receipts[("0192", "LE-MONTHLY", sep)][-1]),
            ResponseIn(
                body="The exposure comprises two term loans and a guarantee to companies under common "
                "ownership; board approvals attached.",
                resubmission_receipt_id=None,
            ),
        ),
    )
    _open(
        at(date(2026, 11, 18), 10, 12),
        "0163",
        "BSD13",
        OCT,
        "Explain FX swap volumes in BSD13",
        "Please explain the foreign exchange swap volumes reported in BSD13 for October.",
        at(date(2026, 11, 19), 17),
        "kowusu",
    )

    # Extensions granted by a senior supervisor.
    for (inst_code, return_code, day), due in EXTENSIONS.items():
        granted = at(date(2026, 11, 11) if inst_code == "0171" else date(2026, 11, 12), 15, 10)
        world.schedule(
            granted,
            f"extension {inst_code} {return_code}",
            lambda s, inst_code=inst_code, return_code=return_code, day=day, due=due: _extend(
                world, s, inst_code, return_code, day, due
            ),
        )

    # Supervisory note on Northgate.
    world.schedule(
        at(date(2026, 11, 19), 16, 20),
        "northgate note",
        lambda s: add_note(
            s,
            ctx,
            world.staff(s, "aboateng"),
            "0231",
            "Two returns outstanding for October. Institution reports a core banking migration; requested a "
            "remediation plan by 28 Nov.",
        ),
    )

    # BSD5A 2027.1: drafted by E. Asante, approved by M. Adjei (Policy), awaiting the second
    # approval. A proposed change to the aligned definition, effective from January 2027.
    bsd5a = draft_of(definition_doc("BSD5A"))
    proposal = DraftIn.model_validate(
        {
            **bsd5a.model_dump(by_alias=True),
            "version": "2027.1",
            "change_summary": "Proposed for 2027: the same capital adequacy lines, with a new "
            "return version so institutions can certify against it before it applies.",
            "first_reporting_date": date(2027, 1, 31),
            "notice_days": 60,
        }
    )
    world.schedule(
        at(date(2026, 11, 18), 10, 5),
        "bsd5a draft",
        lambda s: create_draft(s, ctx, world.staff(s, "easante"), "BSD5A", proposal),
    )
    world.schedule(
        at(date(2026, 11, 19), 11, 30),
        "bsd5a first approval",
        lambda s: approve_version(s, ctx, world.staff(s, "madjei"), "BSD5A", "2027.1"),
    )

    # The chain is verified by the scheduled job at 16:30.
    world.schedule(
        at(date(2026, 11, 21), 16, 30), "verify", lambda s: verify_audit(ctx, trigger="scheduled")
    )

    # API request statistics for the last 30 days.
    def _stats(session: Session) -> None:
        for offset in range(30):
            day = TODAY.date() - timedelta(days=offset)
            rng = rng_for("stats", day.isoformat())
            total = 1800 + rng.randint(0, 900)
            session.add(
                ApiDailyStat(day=day, total=total, server_errors=1 if offset in (4, 17) else 0)
            )

    world.schedule(at(date(2026, 11, 21), 16, 39), "api stats", _stats)


def _query_for(session: Session, receipt_id: str) -> str:
    query = session.scalars(
        select(Query).where(Query.receipt_id == receipt_id).order_by(Query.opened_at.desc())
    ).first()
    assert query is not None, receipt_id
    return query.number


def _extend(
    world: World, session: Session, inst_code: str, return_code: str, day: date, due: datetime
) -> None:
    principal = world.staff(session, "kowusu")
    institution = world.rows[inst_code]
    grant_extension(
        session,
        institution,
        return_code,
        day,
        due,
        "Extension granted on request",
        principal.subject,
        world.clock.now(),
    )
    append(
        session,
        world.ctx.keys,
        world.ctx.clock,
        actor=principal.actor,
        category="decision",
        action=f"Granted extension for {return_code}",
        object_type="obligation",
        object_ref=f"{inst_code} {return_code} {day.isoformat()}",
        institution_code=inst_code,
        detail={"due_at": due.isoformat(), "reporting_date": day.isoformat()},
    )


def plan_access(world: World) -> None:
    ctx = world.ctx
    operator = world.operator

    def _request(
        when: datetime,
        name: str,
        title: str,
        scope: str,
        excludes: str,
        reason: str,
        incident: str | None,
    ) -> None:
        world.schedule(
            when,
            f"access request {name}",
            lambda s: request_access(
                s,
                ctx,
                operator,
                AccessRequestIn(
                    operator_name=name,
                    operator_title=title,
                    scope=scope,
                    excludes=excludes,
                    reason=reason,
                    incident_ref=incident,
                ),
            ),
        )

    _request(
        at(date(2026, 11, 10), 9, 12),
        "Selasie Amoah",
        "AequorOS support engineer",
        "Read-only logs of the receipt service",
        "Filing content, supervisor notes, keys",
        "Investigate a delayed receipt reported by one institution",
        None,
    )
    world.schedule(at(date(2026, 11, 10), 10, 20), "expire", lambda s: access_view(s, ctx))
    _request(
        at(date(2026, 11, 12), 13, 40),
        "Jojo Tetteh",
        "AequorOS support engineer",
        "Read access to all services",
        "Keys",
        "General health check",
        None,
    )
    world.schedule(
        at(date(2026, 11, 12), 14, 5),
        "deny",
        lambda s: deny(
            s,
            ctx,
            world.staff(s, "madjei"),
            _latest_request(s),
            "Reason too broad; request a narrower scope.",
        ),
    )
    _request(
        at(date(2026, 11, 18), 9, 30),
        "Rita Quaye",
        "AequorOS on-call engineer",
        "Read-only logs of the validation service",
        "Filing content, supervisor notes, keys",
        "Validation queue backlog after a deployment",
        "INC-2026-0109",
    )
    world.schedule(
        at(date(2026, 11, 18), 9, 41),
        "approve",
        lambda s: approve(s, ctx, world.staff(s, "madjei"), _latest_request(s), 30),
    )
    world.schedule(at(date(2026, 11, 18), 10, 20), "end", lambda s: access_view(s, ctx))
    # Time-boxed items are placed relative to the moment the seed runs so they are live.
    anchor = max(world.live_now, TODAY + timedelta(minutes=3))
    _request(
        anchor - timedelta(minutes=9),
        "Jojo Tetteh",
        "AequorOS support engineer",
        "Read-only diagnostics",
        "Filing content",
        "Slow receipts reported by two institutions",
        None,
    )
    world.schedule(
        anchor - timedelta(minutes=6),
        "approve live",
        lambda s: approve(s, ctx, world.staff(s, "madjei"), _latest_request(s), 60),
    )
    _request(
        anchor - timedelta(minutes=2),
        "Rita Quaye",
        "AequorOS on-call engineer",
        "Read-only logs of the validation service",
        "Filing content, supervisor notes, keys",
        "Receipt times above 5 s since 16:20",
        "INC-2026-0117",
    )


def _latest_request(session: Session) -> str:
    from app.db.models import OperatorAccessRequest

    row = session.scalars(
        select(OperatorAccessRequest).order_by(OperatorAccessRequest.requested_at.desc())
    ).first()
    assert row is not None
    return row.number


__all__ = ["ApiClient", "Institution", "Submission", "plan"]
