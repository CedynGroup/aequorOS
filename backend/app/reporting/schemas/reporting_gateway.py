"""Schemas for a bank's connection to the AequorOS Reporting Gateway.

Banks only read their connection: enrolment is carried out by the channel
operator from the staff console (``GatewayEnrolmentRequest``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.reporting.schemas.packages import ClosedModel

type GatewayStepKey = Literal[
    "enrolled", "signing_key_created", "connection_tested", "first_filing"
]
type GatewayEnvironment = Literal["sandbox", "production"]
type SigningKeyHolder = Literal["aequoros_vault", "bank_key_service"]


class GatewayConnectionFacts(ClosedModel):
    """The bank's API client on the gateway. Its key never leaves the vault."""

    client_id: str
    environment: GatewayEnvironment
    #: When the client certificate expires (its ``notAfter``).
    renews_by: datetime
    fingerprint_sha256: str
    #: When AequorOS last replaced the certificate, if it ever has.
    renewed_at: datetime | None = None
    #: From when AequorOS renews it automatically. Set only while the staff plane's
    #: renewal check is running for this bank; ``None`` means renewal is a staff act.
    automatic_renewal_from: datetime | None = None


class GatewaySigningKeyFacts(ClosedModel):
    kid: str
    algorithm: Literal["ES256"]
    held_by: SigningKeyHolder
    created_at: datetime | None
    last_used_at: datetime | None


class GatewayStep(ClosedModel):
    key: GatewayStepKey
    done: bool


class GatewayTransmission(ClosedModel):
    package_id: UUID
    return_code: str
    reporting_date: date
    approved_by_role: str | None
    sent_at: datetime
    receipt_id: str
    gateway_status: str


class ReportingGatewayStatus(ClosedModel):
    enrolled: bool
    institution_code: str | None
    enrolled_at: datetime | None
    connection: GatewayConnectionFacts | None
    signing_key: GatewaySigningKeyFacts | None
    last_connection_test_at: datetime | None
    last_connection_test_ok: bool | None
    steps: list[GatewayStep]
    transmissions: list[GatewayTransmission]


class GatewayConnectionTest(ClosedModel):
    ok: bool
    gateway_keys: int
    tested_at: datetime
    message: str


class GatewayContact(ClosedModel):
    role: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")


class GatewayEnrolmentRequest(ClosedModel):
    """Enrol a bank on the gateway (channel operator only).

    ``institution_code`` defaults to the code in the bank's institution profile.
    Enrolment is idempotent: repeating it reuses the bank's keys and client.
    """

    institution_code: str | None = Field(default=None, pattern=r"^\d{4}$")
    contacts: list[GatewayContact] = Field(default_factory=list, max_length=10)


type GatewayRenewalStatus = Literal[
    "in_progress", "failed", "revocation_pending", "complete", "cancelled"
]
type GatewayRenewalStage = Literal["key_prepared", "client_issued", "switched", "previous_revoked"]
type GatewayRenewalTrigger = Literal["staff", "scheduled"]


class GatewayCertificateRenewalRead(ClosedModel):
    """The latest renewal of a bank's gateway client certificate (staff console).

    ``revocation_pending``: the bank already uses the new certificate; the old
    client is still to be revoked on the gateway and is retried.
    """

    status: GatewayRenewalStatus
    stage: GatewayRenewalStage
    trigger: GatewayRenewalTrigger
    requested_by: str
    reason: str
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    previous_client_id: str | None = None
    previous_not_after: datetime | None = None
    new_client_id: str | None = None
    previous_revoked_at: datetime | None = None
    error: str | None = None
    failures: int = 0


class GatewayCertificateRenewalRequest(ClosedModel):
    """Renew (or resume renewing) a bank's gateway client certificate now."""

    reason: str = Field(min_length=3, max_length=500)


class GatewayEnrolmentRead(ClosedModel):
    """What the staff console shows for a bank's gateway enrolment."""

    bank_id: str
    enrolled: bool
    institution_code: str | None
    environment: GatewayEnvironment | None
    client_id: str | None
    certificate_not_after: datetime | None
    signing_kid: str | None
    enrolled_at: datetime | None
    last_connection_test_at: datetime | None
    last_connection_test_ok: bool | None
    message: str | None = None
    #: From when the certificate is due for renewal (expiry less the renewal window).
    renewal_due_from: datetime | None = None
    #: When the staff plane's scheduled check last looked at this bank.
    last_renewal_check_at: datetime | None = None
    certificate_renewal: GatewayCertificateRenewalRead | None = None


# --- every institution on the channel (staff console) -------------------------------

type GatewayInstitutionType = Literal[
    "universal_bank", "rural_bank", "savings_and_loans", "microfinance", "other"
]
type GatewayInstitutionStatus = Literal["enrolled", "live", "suspended"]


class GatewayInstitutionClient(ClosedModel):
    client_id: str
    environment: GatewayEnvironment
    certificate_not_after: datetime
    revoked: bool


class GatewayInstitutionKey(ClosedModel):
    kid: str
    status: Literal["active", "retired", "revoked"]
    registered_at: datetime


class GatewayLinkedBank(ClosedModel):
    """The AequorOS client bank enrolled under this institution code, if any."""

    organization_id: str
    bank_id: str
    name: str


class GatewayInstitution(ClosedModel):
    code: str
    name: str
    type: GatewayInstitutionType
    status: GatewayInstitutionStatus
    enrolled_at: datetime
    clients: list[GatewayInstitutionClient]
    signing_keys: list[GatewayInstitutionKey]
    #: Set when the institution is an AequorOS client bank, whose keys AequorOS holds.
    aequoros_bank: GatewayLinkedBank | None = None


class GatewayInstitutionList(ClosedModel):
    items: list[GatewayInstitution]


class GatewayInstitutionCreate(ClosedModel):
    """Enrol an institution that files with its own software (idempotent on ``code``)."""

    code: str = Field(pattern=r"^\d{4}$")
    name: str = Field(min_length=2, max_length=200)
    type: GatewayInstitutionType
    contacts: list[GatewayContact] = Field(default_factory=list, max_length=10)


class GatewaySoftware(ClosedModel):
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)


class GatewayClientIssue(ClosedModel):
    """Issue an API client from the institution's own certificate request and key.

    The institution generates its key pair: AequorOS never sees its private key.
    ``public_jwk`` is the P-256 public key of the CSR, which it also signs its
    client assertions with.
    """

    environment: GatewayEnvironment
    csr_pem: str = Field(min_length=100, max_length=20_000)
    public_jwk: dict[str, str]
    software: GatewaySoftware | None = None


class GatewayClientIssued(ClosedModel):
    """What to hand to the institution: its client id and certificate (public)."""

    client_id: str
    certificate_pem: str
    certificate_chain_pem: str
    not_after: datetime
    scopes: list[str]


class GatewaySigningKeyRegister(ClosedModel):
    """Register the institution's filing signature key (its public key only)."""

    kid: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    public_jwk: dict[str, str]


class GatewayInstitutionStatusChange(ClosedModel):
    status: Literal["live", "suspended"]
    reason: str = Field(min_length=3, max_length=500)


class GatewayClientRevoke(ClosedModel):
    reason: str = Field(min_length=3, max_length=500)


# -- the regulator's queries on the bank's filings ----------------------------------

type RegulatorQueryStatus = Literal["awaiting_institution", "awaiting_supervisor", "closed"]


class RegulatorQueryMessageRead(ClosedModel):
    id: str
    author: Literal["supervisor", "institution", "system"]
    author_label: str
    body: str
    created_at: datetime


class RegulatorQueryFilingRead(ClosedModel):
    """The bank's own return the query is about, when the receipt is one it filed."""

    package_id: UUID
    return_code: str
    reporting_date: date
    version: int


class RegulatorQueryRead(ClosedModel):
    number: str
    kind: Literal["query", "resubmission"]
    subject: str
    status: RegulatorQueryStatus
    receipt_id: str
    due_at: datetime | None
    opened_at: datetime
    acknowledged_at: datetime | None
    messages: list[RegulatorQueryMessageRead]
    filing: RegulatorQueryFilingRead | None


class RegulatorQueryListRead(ClosedModel):
    """The regulator's queries, read live from the AequorOS Reporting Gateway.

    ``connected`` is false when the bank is not enrolled on the gateway; ``message``
    then (or when the gateway could not be reached) says what to do.
    """

    connected: bool
    items: list[RegulatorQueryRead]
    message: str | None
    read_at: datetime


class RegulatorQueryResponseCreate(ClosedModel):
    body: str = Field(min_length=1, max_length=20000)
    #: A corrected filing that answers a request for resubmission.
    resubmission_receipt_id: str | None = Field(default=None, max_length=40)
