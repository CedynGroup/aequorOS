"""The design's synthetic world, built through the real service paths.

Every institution, client certificate and signing key is enrolled through the enrolment
service (as the operator API does); every filing is signed with the institution's own
key and submitted through the filing service, so receipts, ledger entries and validation
results are genuine; decisions, queries, extensions, operator sessions and the BSD5A
2026.2 draft go through their services as the synthetic staff. Events run in time order
on an injected clock centred on 21 Nov 2026 (the design's "today").
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from cryptography import x509
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import Actor
from app.auth.staff import StaffPrincipal
from app.core.clock import SettableClock, add_business_days, utc
from app.core.context import AppContext
from app.db.models import ApiClient, Institution, Submission
from app.enrolment.service import (
    ContactIn,
    EnrolInstitutionRequest,
    IssueClientRequest,
    JwksIn,
    RegisterKeyRequest,
    SoftwareIn,
    enrol_institution,
    issue_client,
    register_signing_key,
)
from app.filing.obligations import due_at as default_due_at
from app.filing.service import FilerContext, submit
from app.filing.validation import run_validation
from app.portal.submissions import accept
from app.returns.catalogue_loader import CatalogueDocument, document
from app.seed.catalogue_payloads import corrupt as catalogue_corrupt
from app.seed.catalogue_payloads import payload as catalogue_payload
from app.seed.client import InstitutionKeys, build_filing, csr_pem, declaration
from app.seed.payloads import rng_for, scale_at
from app.seed.staff import sign_in

TODAY = datetime(2026, 11, 21, 16, 40, tzinfo=UTC)
OCT = date(2026, 10, 31)
INBOX_WINDOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
OCT_CODES = (
    "BSD2",
    "BSD3A",
    "BSD5A",
    "BSD6",
    "BSD7A",
    "BSD8",
    "BSD13",
    "CAR-RWA",
    "LMT",
    "NPL-MONTHLY",
)
MONTHLY = (
    "BSD6",
    "BSD5A",
    "BSD3A",
    "BSD7A",
    "BSD8",
    "BSD13",
    "CAR-RWA",
    "LMT",
    "LE-MONTHLY",
    "NPL-MONTHLY",
    "BSD2",
    "SDI-LMT-MONTHLY",
    "SDI-LE-MONTHLY",
)
#: When each return starts in the seeded world's timeline (the design's pilot dates).
#: The definitions themselves apply from 2025; the world files them from these dates.
SEED_START: dict[str, date] = {
    "DBK-DAILY": date(2026, 11, 2),
    "BSD1": date(2026, 10, 30),
    "CAR-RWA": date(2026, 9, 30),
    "LMT": date(2026, 9, 30),
    "IRRBB-PILOT": date(2026, 6, 30),
    "SDI-IRRBB-QUARTERLY": date(2026, 6, 30),
}
DEFAULT_START = date(2025, 11, 30)


def definition_doc(code: str) -> CatalogueDocument:
    """The aligned definition the world files ``code`` under."""
    doc = document(code)
    if doc is None:
        raise KeyError(f"the catalogue carries no {code} definition")
    return doc


TARGET_LAST_RECEIPT = 114822
TARGET_RIVERBEND_QUERY = 318
UNIVERSAL, RURAL, SAVINGS, MICRO = (
    "universal_bank",
    "rural_bank",
    "savings_and_loans",
    "microfinance",
)
AEQUOROS, IN_HOUSE = "AequorOS", "In-house system"


def at(day: date, hh: int, mm: int = 0, ss: int = 0, ms: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm, ss, ms * 1000), UTC)


@dataclass
class Inst:
    code: str
    name: str
    type: str
    channel: str | None
    software: str | None
    supervisor: str | None
    stage: str = "live"
    live_since: date | None = date(2025, 11, 20)
    client_id: str | None = None
    not_after: date | None = None
    client_issued: date | None = None
    portal_users: int = 2
    flags: list[str] = field(default_factory=lambda: [])
    contacts: list[tuple[str, str]] = field(default_factory=lambda: [])
    base: int = 1_000_000
    keys: InstitutionKeys = field(default_factory=InstitutionKeys)
    signing_registered: date | None = None
    environment: str = "production"
    # filled while seeding
    thumbprint: str | None = None
    cert_not_after: datetime | None = None
    rotated: tuple[str, date, date] | None = None  # (new client id, issued, not_after)

    @property
    def slug(self) -> str:
        return (self.client_id or f"{self.code}-x").split("-")[0]


NAMED: list[Inst] = [
    Inst(
        "0142",
        "Meridian Coast Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        "kowusu",
        client_id="mcb-prod-01",
        not_after=date(2027, 6, 14),
        client_issued=date(2025, 11, 10),
        portal_users=4,
        base=5_200_000,
        contacts=[("Compliance officer", "Efua Mensah"), ("Technical contact", "Nii Armah")],
    ),
    Inst(
        "0155",
        "Ridgeway Commercial Bank",
        UNIVERSAL,
        "api",
        IN_HOUSE,
        "kowusu",
        client_id="rcb-prod-01",
        not_after=date(2026, 11, 11),
        client_issued=date(2025, 11, 11),
        portal_users=2,
        base=3_400_000,
        contacts=[("Compliance officer", "Adwoa Sarpong"), ("Technical contact", "Kwame Ofori")],
    ),
    Inst(
        "0117",
        "Palmgrove Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        "aboateng",
        client_id="pgb-prod-01",
        not_after=date(2027, 3, 14),
        client_issued=date(2025, 11, 12),
        portal_users=3,
        base=3_900_000,
        signing_registered=date(2026, 2, 2),
        contacts=[("Compliance officer", "Ama Owusu-Ansah"), ("Technical contact", "Kojo Badu")],
    ),
    Inst(
        "0128",
        "Harbourline Bank",
        UNIVERSAL,
        "api",
        IN_HOUSE,
        "kowusu",
        client_id="hbl-prod-01",
        not_after=date(2027, 1, 19),
        client_issued=date(2025, 11, 13),
        portal_users=5,
        base=4_600_000,
        contacts=[("Compliance officer", "Esinam Dogbe"), ("Technical contact", "Yaw Boakye")],
    ),
    Inst(
        "0163",
        "Kestrel Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        "kowusu",
        client_id="kes-prod-01",
        not_after=date(2026, 11, 30),
        client_issued=date(2025, 11, 14),
        portal_users=3,
        base=2_800_000,
        contacts=[("Compliance officer", "Akua Frimpong"), ("Technical contact", "Selorm Agbeko")],
    ),
    Inst(
        "0231",
        "Northgate Savings & Loans",
        SAVINGS,
        "portal_upload",
        None,
        "aboateng",
        portal_users=3,
        flags=["heightened_monitoring"],
        base=420_000,
        contacts=[("Compliance officer", "Abena Darko"), ("Technical contact", "Yaw Mensah")],
    ),
    Inst(
        "0312",
        "Riverbend Rural Bank",
        RURAL,
        "portal_upload",
        None,
        "kowusu",
        portal_users=2,
        base=180_000,
        contacts=[("Compliance officer", "Kafui Amedzro"), ("Technical contact", "Eric Tsikata")],
    ),
    Inst(
        "0171",
        "Savanna Union Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        "aboateng",
        client_id="svu-prod-01",
        not_after=date(2027, 4, 2),
        client_issued=date(2025, 11, 15),
        base=2_300_000,
        contacts=[
            ("Compliance officer", "Rahinatu Issah"),
            ("Technical contact", "Abdul-Rauf Seidu"),
        ],
    ),
    Inst(
        "0184",
        "Cedarwood Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        "aboateng",
        client_id="cdw-prod-01",
        not_after=date(2027, 5, 21),
        client_issued=date(2025, 11, 15),
        base=2_100_000,
        contacts=[("Compliance officer", "Dzifa Kpodo"), ("Technical contact", "Edem Klu")],
    ),
    Inst(
        "0136",
        "Lakeside Trust Bank",
        UNIVERSAL,
        "portal_upload",
        None,
        "kowusu",
        portal_users=2,
        base=1_700_000,
        contacts=[
            ("Compliance officer", "Naa Okailey Tetteh"),
            ("Technical contact", "Bright Asamoah"),
        ],
    ),
    Inst(
        "0149",
        "Goldfield Commercial Bank",
        UNIVERSAL,
        "api",
        IN_HOUSE,
        "kowusu",
        client_id="gcb-prod-01",
        not_after=date(2027, 8, 30),
        client_issued=date(2025, 11, 16),
        base=3_100_000,
        contacts=[("Compliance officer", "Maame Yeboah"), ("Technical contact", "Fiifi Quansah")],
    ),
    Inst(
        "0192",
        "Summit Mutual Bank",
        UNIVERSAL,
        "api",
        IN_HOUSE,
        "aboateng",
        client_id="smb-prod-01",
        not_after=date(2027, 2, 9),
        client_issued=date(2025, 11, 16),
        base=1_500_000,
        contacts=[("Compliance officer", "Gifty Antwi"), ("Technical contact", "Prince Opoku")],
    ),
]

GENERATED_NAMES: list[tuple[str, str]] = [
    *(
        (n, UNIVERSAL)
        for n in (
            "Amberline Bank",
            "Bluefin Bank",
            "Copperleaf Bank",
            "Driftstone Bank",
            "Emberwood Bank",
            "Foxglove Bank",
            "Greywater Bank",
            "Highmoor Bank",
            "Ivorygate Bank",
            "Larkspur Bank",
        )
    ),
    *(
        (n, RURAL)
        for n in (
            "Ashgrove Rural Bank",
            "Brookvale Rural Bank",
            "Clearwater Rural Bank",
            "Dovecote Rural Bank",
            "Elmfield Rural Bank",
            "Fennel Valley Rural Bank",
            "Glenmoor Rural Bank",
            "Hazelbrook Rural Bank",
            "Millbrook Rural Bank",
            "Oakhollow Rural Bank",
            "Pinecrest Rural Bank",
            "Quarry Hill Rural Bank",
            "Stillwater Rural Bank",
            "Thornfield Rural Bank",
            "Willowmere Rural Bank",
        )
    ),
    *(
        (n, SAVINGS)
        for n in (
            "Alderbank Savings & Loans",
            "Beacon Ridge Savings & Loans",
            "Coralstone Savings & Loans",
            "Dunmore Savings & Loans",
            "Eastbrook Savings & Loans",
            "Fairhaven Savings & Loans",
            "Silverleaf Savings & Loans",
            "Tidewater Savings & Loans",
            "Westmere Savings & Loans",
            "Yarrow Savings & Loans",
            "Zephyr Savings & Loans",
        )
    ),
]

PIPELINE: list[Inst] = [
    Inst(
        "0451",
        "Equinox Microfinance",
        MICRO,
        "api",
        IN_HOUSE,
        None,
        stage="sandbox",
        live_since=None,
        client_id="eqx-sbx-01",
        not_after=date(2027, 10, 1),
        client_issued=date(2026, 10, 1),
        portal_users=1,
        environment="sandbox",
    ),
    Inst(
        "0246",
        "Crestview Savings & Loans",
        SAVINGS,
        "api",
        AEQUOROS,
        None,
        stage="sandbox",
        live_since=None,
        client_id="cvs-sbx-01",
        not_after=date(2027, 10, 20),
        client_issued=date(2026, 10, 20),
        portal_users=2,
        environment="sandbox",
    ),
    Inst(
        "0327",
        "Lanternfield Rural Bank",
        RURAL,
        "api",
        AEQUOROS,
        None,
        stage="sandbox",
        live_since=None,
        client_id="lrb-sbx-01",
        not_after=date(2027, 11, 3),
        client_issued=date(2026, 11, 3),
        portal_users=1,
        environment="sandbox",
    ),
    Inst(
        "0201",
        "Orchard Lane Bank",
        UNIVERSAL,
        "api",
        AEQUOROS,
        None,
        stage="certified",
        live_since=None,
        client_id="olb-prod-01",
        not_after=date(2027, 11, 12),
        client_issued=date(2026, 11, 12),
        portal_users=2,
    ),
    Inst(
        "0463",
        "Sunpath Microfinance",
        MICRO,
        "api",
        IN_HOUSE,
        None,
        stage="certified",
        live_since=None,
        client_id="spm-prod-01",
        not_after=date(2027, 11, 16),
        client_issued=date(2026, 11, 16),
        portal_users=1,
    ),
    Inst(
        "0209",
        "Brightwater Bank",
        UNIVERSAL,
        None,
        None,
        None,
        stage="requested",
        live_since=None,
        portal_users=0,
    ),
    Inst(
        "0471",
        "Marigold Microfinance",
        MICRO,
        None,
        None,
        None,
        stage="requested",
        live_since=None,
        portal_users=0,
    ),
]

OCT_PATTERNS: dict[str, str] = {
    "0231": "OXXLOXOOLX",
    "0128": "OOXOOOLOXO",
    "0312": "OLOOXOOOOL",
    "0163": "OOOOOLOXOO",
    "0117": "LOOOOOOOXO",
    "0171": "OOOLOOOOOX",
    "0184": "OOOOOOLOOO",
    "0142": "OOOOOOOOOL",
    "0155": "OOOOOOOOOO",
    "0136": "OOLOOOOOOO",
    "0149": "OOOOOOOOOO",
    "0192": "OOOOONOOON",
}
#: October states for returns outside the design's original ten (the SDI returns).
OCT_EXTRA: dict[tuple[str, str], str] = {("0231", "SDI-LE-MONTHLY"): "X"}
NORTHGATE_HISTORY: dict[str, str] = {  # Dec 2025 … Oct 2026, design Institution.dc.html
    "SDI-LMT-MONTHLY": "OOOLOOLOOLO",
    "SDI-LE-MONTHLY": "OOOOLOOLOOX",
    "NPL-MONTHLY": "OOLOOOOOLOX",
}
DesignTiming = tuple[datetime, str]
INBOX: dict[tuple[str, str], DesignTiming] = {
    ("0149", "BSD3A"): (at(date(2026, 11, 20), 10, 15, 7), "accept:kowusu"),
    ("0231", "SDI-LMT-MONTHLY"): (at(date(2026, 11, 20), 11, 48, 31), "northgate"),
    ("0163", "BSD5A"): (at(date(2026, 11, 20), 14, 3, 12), "accept:kowusu"),
    ("0192", "LE-MONTHLY"): (at(date(2026, 11, 20), 15, 12, 40), "reject"),
    ("0184", "NPL-MONTHLY"): (at(date(2026, 11, 20), 16, 30, 5), "review"),
    ("0117", "BSD2"): (at(date(2026, 11, 20), 17, 55, 2), "palmgrove"),
    ("0171", "BSD13"): (at(date(2026, 11, 21), 9, 5, 18), "accept:aboateng"),
    ("0312", "NPL-MONTHLY"): (at(date(2026, 11, 21), 11, 2, 44), "riverbend"),
    ("0136", "BSD6"): (at(date(2026, 11, 21), 13, 40, 51), "accept:kowusu"),
    ("0128", "LMT"): (at(date(2026, 11, 21), 14, 12, 9), "reject"),
    ("0155", "CAR-RWA"): (at(date(2026, 11, 21), 16, 21, 2), "accept:kowusu:16:21:40"),
    ("0142", "BSD2"): (at(date(2026, 11, 21), 16, 38, 4), "validating"),
}


@dataclass(order=True)
class Event:
    when: datetime
    order: int
    label: str = field(compare=False)
    run: Callable[[Session], object] = field(compare=False)


class World:
    def __init__(self, ctx: AppContext, clock: SettableClock, *, live_now: datetime) -> None:
        self.ctx = ctx
        self.clock = clock
        self.live_now = live_now
        self.events: list[Event] = []
        self.counter = itertools.count()
        self.institutions: dict[str, Inst] = {}
        self.rows: dict[str, Institution] = {}
        self.data: dict[tuple[str, str, date], dict[str, JsonValue]] = {}
        self.receipts: dict[tuple[str, str, date], list[str]] = {}
        self.operator = Actor(
            "operator", "dev-operator-01", "Channel operator", "Operator client dev-operator-01"
        )
        self.principals: dict[tuple[str, date], StaffPrincipal] = {}
        self.filings_2026 = 0
        self.queries_2026_before_riverbend = 0
        self.riverbend_query_at = at(date(2026, 11, 21), 11, 30, 2)
        self.log: list[str] = []

    # --- scheduling ------------------------------------------------------------------
    def schedule(self, when: datetime, label: str, run: Callable[[Session], object]) -> None:
        heapq.heappush(self.events, Event(utc(when), next(self.counter), label, run))

    def staff(self, session: Session, subject: str) -> StaffPrincipal:
        key = (subject, self.clock.now().date())
        principal = self.principals.get(key)
        if principal is None:
            principal = sign_in(session, self.ctx, subject)
            self.principals[key] = principal
        return principal

    # --- payloads ------------------------------------------------------------------------
    def payload(self, inst: Inst, code: str, day: date) -> dict[str, JsonValue]:
        """The institution's figures for ``code`` at ``day``, in the aligned definition's shape."""
        key = (inst.code, code, day)
        if key not in self.data:
            scale = scale_at(inst.base, inst.code, day)
            rng = rng_for(inst.code, code, day.isoformat())
            self.data[key] = catalogue_payload(definition_doc(code), scale, rng)
        return self.data[key]

    # --- enrolment -------------------------------------------------------------------------
    def enrol(self, session: Session, inst: Inst, when: datetime) -> None:
        enrol_institution(
            session,
            self.ctx,
            self.operator,
            EnrolInstitutionRequest(
                code=inst.code,
                name=inst.name,
                type=inst.type,  # pyright: ignore[reportArgumentType]
                contacts=[
                    ContactIn(role=r, name=n, email=f"{n.split()[0].lower()}@{inst.slug}.example")
                    for r, n in inst.contacts
                ],
            ),
            at=when,
        )
        row = session.scalars(select(Institution).where(Institution.code == inst.code)).one()
        row.supervisor_subject = inst.supervisor
        row.portal_users = inst.portal_users
        row.flags = list(inst.flags)
        if inst.channel == "portal_upload":
            row.channel = "portal_upload"
        self.rows[inst.code] = row
        kid_prefix = (
            inst.client_id.split("-")[0] if inst.client_id else inst.name.split()[0][:3].lower()
        )
        inst.keys = InstitutionKeys(
            client_kid=f"{kid_prefix}-client-1", signing_kid=f"{kid_prefix}-sign-2026-01"
        )

    def issue(
        self, session: Session, inst: Inst, client_id: str, issued: date, not_after: date
    ) -> None:
        software = None
        if inst.software:
            software = SoftwareIn(
                name=inst.software, version="2026.11" if inst.software == AEQUOROS else "4.2"
            )
        _, client = issue_client(
            session,
            self.ctx,
            self.operator,
            inst.code,
            IssueClientRequest(
                environment="sandbox" if inst.environment == "sandbox" else "production",
                csr_pem=csr_pem(inst.keys.client_key, client_id),
                jwks=JwksIn.model_validate(inst.keys.client_jwks()),
                software=software,
            ),
            preferred_client_id=client_id,
            at=at(issued, 10),
        )
        row = session.scalars(
            select(ApiClient).where(ApiClient.client_id == client.client_id)
        ).one()
        # The certificate validity is the one the design shows for this client.
        certificate = self._reissue(row, inst, not_after)
        inst.thumbprint = row.certificate_thumbprint
        inst.cert_not_after = row.certificate_not_after
        del certificate

    def _reissue(self, row: ApiClient, inst: Inst, not_after: date) -> x509.Certificate:
        from app.core.certs import describe_certificate
        from app.enrolment.ca import require_ca

        ca = require_ca(self.ctx.ca)
        issued = row.created_at
        certificate = ca.issue(
            inst.keys.client_key.public_key(),
            common_name=row.client_id,
            organisation=inst.name,
            not_before=issued - timedelta(minutes=5),
            not_after=at(not_after, 23, 59, 59),
        )
        described = describe_certificate(certificate)
        row.certificate_pem = described.pem
        row.certificate_thumbprint = described.thumbprint
        row.certificate_serial = described.serial
        row.certificate_not_after = described.not_after
        return certificate

    def register_key(self, session: Session, inst: Inst, when: datetime) -> None:
        register_signing_key(
            session,
            self.ctx,
            self.operator,
            inst.code,
            RegisterKeyRequest(kid=inst.keys.signing_kid, public_jwk=inst.keys.signing_jwk()),
            at=when,
        )

    # --- filing ------------------------------------------------------------------------------
    def filer(self, inst: Inst) -> FilerContext:
        row = self.rows[inst.code]
        if inst.channel == "api" and inst.client_id:
            actor = Actor("institution", inst.client_id, inst.name, f"API client {inst.client_id}")
            return FilerContext(
                institution=row,
                actor=actor,
                channel="api",
                environment=inst.environment,
                client_ref=inst.client_id,
                client_id=inst.client_id,
                certificate_thumbprint=inst.thumbprint,
                certificate_not_after=inst.cert_not_after,
            )
        users = f"{inst.portal_users} user{'s' if inst.portal_users != 1 else ''}"
        actor = Actor("institution", f"portal:{inst.code}", inst.name, f"Portal upload · {users}")
        return FilerContext(
            institution=row,
            actor=actor,
            channel="portal_upload",
            environment="production",
            client_ref=f"portal:{inst.code}",
            client_id=None,
            certificate_thumbprint=None,
            certificate_not_after=None,
        )

    def software_for(self, inst: Inst, when: datetime) -> tuple[str, str] | None:
        if inst.channel != "api" or not inst.software:
            return None
        if inst.software == AEQUOROS:
            return AEQUOROS, f"{when.year}.{when.month}"
        return IN_HOUSE, "4.2"

    def file(
        self,
        session: Session,
        inst: Inst,
        code: str,
        day: date,
        *,
        payload: dict[str, JsonValue] | None = None,
        supersedes: str | None = None,
        reason: str | None = None,
        validate: bool = True,
    ) -> Submission:
        now = self.clock.now()
        doc = definition_doc(code)
        body = build_filing(
            signing_key=inst.keys.signing_key,
            kid=inst.keys.signing_kid,
            institution_code=inst.code,
            return_code=code,
            return_version=doc.version,
            reporting_date=day,
            payload=payload if payload is not None else self.payload(inst, code, day),
            declaration_body=declaration(
                approved_at=now - timedelta(minutes=6),
                amendment_reason=reason,
                software=self.software_for(inst, now),
            ),
            created_at=now - timedelta(seconds=3),
            supersedes_receipt_id=supersedes,
        )
        path = f"/v1/submissions/{supersedes}/amendments" if supersedes else "/v1/submissions"
        outcome = submit(
            session,
            self.ctx,
            self.filer(inst),
            body,
            path=path,
            idempotency_key=f"seed-{inst.code}-{code}-{day.isoformat()}-{now.timestamp():.3f}",
            amendment_of=supersedes,
        )
        assert outcome.submission_id is not None
        sub = session.get(Submission, outcome.submission_id)
        assert sub is not None
        self.receipts.setdefault((inst.code, code, day), []).append(sub.receipt_id)
        if validate:
            submission_id = sub.id

            def _validate(s: Session) -> None:
                target = s.get(Submission, submission_id)
                assert target is not None
                run_validation(s, self.ctx, target)

            self.schedule(now + timedelta(seconds=3), f"validate {sub.receipt_id}", _validate)
        return sub

    def plan_file(
        self,
        when: datetime,
        inst: Inst,
        code: str,
        day: date,
        after: Callable[[Session, Submission], None] | None = None,
        **kwargs: object,
    ) -> None:
        if when.year == 2026:
            self.filings_2026 += 1

        def _run(session: Session) -> None:
            sub = self.file(session, inst, code, day, **kwargs)  # pyright: ignore[reportArgumentType]
            if after is not None:
                after(session, sub)

        self.schedule(when, f"file {inst.code} {code} {day}", _run)

    def plan_accept(self, when: datetime, inst: Inst, code: str, day: date, subject: str) -> None:
        def _run(session: Session) -> None:
            receipt = self.receipts[(inst.code, code, day)][-1]
            sub = session.scalars(select(Submission).where(Submission.receipt_id == receipt)).one()
            if sub.status == "in_review":
                accept(session, self.ctx, self.staff(session, subject), receipt, None)

        self.schedule(when, f"accept {inst.code} {code} {day}", _run)

    def plan_query(self, when: datetime, label: str, run: Callable[[Session], object]) -> None:
        if when.year == 2026 and when < self.riverbend_query_at:
            self.queries_2026_before_riverbend += 1
        self.schedule(when, label, run)

    def count_query(self, when: datetime) -> None:
        if when.year == 2026 and when < self.riverbend_query_at:
            self.queries_2026_before_riverbend += 1

    # --- helpers for timing -----------------------------------------------------------------
    def due(self, code: str, day: date, inst: Inst | None = None) -> date:
        if inst is not None and (inst.code, code, day) in self.extensions:
            return self.extensions[(inst.code, code, day)].date()
        return default_due_at(self.ctx.settings, code, definition_doc(code).cadence, day).date()

    @property
    def extensions(self) -> dict[tuple[str, str, date], datetime]:
        return EXTENSIONS

    def on_time(self, inst: Inst, code: str, day: date, order: int) -> datetime:
        rng = rng_for("ontime", inst.code, day.isoformat())
        due = self.due(code, day)
        start = add_business_days(day, 1)
        back = rng.randint(0, 4)
        filing_day = due
        for _ in range(back):
            previous = filing_day - timedelta(days=1)
            while previous.weekday() >= 5:
                previous -= timedelta(days=1)
            if previous < start:
                break
            filing_day = previous
        hour = 9 + rng.randint(0, 5)
        return at(filing_day, hour, rng.randint(0, 50)) + timedelta(
            minutes=order * 7, seconds=rng.randint(0, 59)
        )

    def late(self, inst: Inst, code: str, day: date, latest: datetime | None = None) -> datetime:
        rng = rng_for("late", inst.code, code, day.isoformat())
        due = self.due(code, day, inst)
        when = at(
            due + timedelta(days=rng.randint(1, 5)),
            9 + rng.randint(0, 7),
            rng.randint(0, 59),
            rng.randint(0, 59),
        )
        if latest is not None and when >= latest:
            when = latest - timedelta(hours=rng.randint(2, 40))
            if when.date() <= due:
                when = at(due + timedelta(days=1), 15, rng.randint(0, 59))
        return when

    def accept_after(self, when: datetime, inst: Inst, salt: str) -> datetime:
        rng = rng_for("accept", inst.code, salt)
        day = add_business_days(when.date(), rng.randint(1, 2))
        return at(day, 10 + rng.randint(0, 6), rng.randint(0, 59), rng.randint(0, 59))


EXTENSIONS: dict[tuple[str, str, date], datetime] = {
    ("0192", "BSD8", OCT): at(date(2026, 11, 27), 23, 59, 59),
    ("0192", "NPL-MONTHLY", OCT): at(date(2026, 11, 27), 23, 59, 59),
    ("0171", "BSD13", OCT): at(date(2026, 11, 18), 23, 59, 59),
}

QUERY_TEMPLATES: list[tuple[str, str, str]] = [
    (
        "Movement in deposits",
        "Please explain the movement in deposits compared with the previous month.",
        "The movement reflects seasonal salary inflows and the maturity of two fixed deposits. Schedules attached.",
    ),
    (
        "Classification of restructured facilities",
        "Please confirm how restructured facilities are classified in this return.",
        "Restructured facilities remain classified at their pre-restructuring grade for six months, per our credit policy.",
    ),
    (
        "Large placement with another bank",
        "Please give the counterparty and maturity of the largest interbank placement.",
        "The placement matures in 30 days with a domestic bank; the term sheet is attached.",
    ),
    (
        "Increase in other assets",
        "Please explain the increase in other assets reported this month.",
        "The increase is prepaid rent and a receivable from the payment switch, settled on the 3rd.",
    ),
    (
        "Provisioning on doubtful loans",
        "Please confirm the provisioning rate applied to doubtful loans.",
        "We applied the required rate; the provisioning schedule is attached.",
    ),
    (
        "Foreign currency position",
        "Please explain the change in the net foreign currency position.",
        "The change follows a customer import payment funded from our dollar nostro balance.",
    ),
    (
        "Operating expenses",
        "Please explain the rise in operating expenses in the period.",
        "The rise is a one-off technology licence renewal paid in the month.",
    ),
]


def corrupt(code: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Deliberately inconsistent figures for the design's rejected filings."""
    return catalogue_corrupt(definition_doc(code), body)
