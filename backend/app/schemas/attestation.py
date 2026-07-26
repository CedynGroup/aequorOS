from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type SigningRole = Literal["preparer", "approver", "board", "witness"]
type AttestationState = Literal["unsigned", "preparer_certified", "fully_certified", "void"]
type BindingClass = Literal["engine_run", "master_data"]
type StepUpMethod = Literal["oidc_reauth", "password_reauth"]
#: Roles that can be PLACED on the artifact. Narrower than ``SigningRole`` on
#: purpose: only ``Sig_Preparer`` and ``Sig_Approver`` exist on the document, so a
#: board placement is a 422 at the contract boundary rather than a 409 later.
type PlaceableRole = Literal["preparer", "approver"]
type PlacementSource = Literal[
    "package", "bank_template", "organization_template", "default"
]
#: What a placed box prints. Only ``signature`` is a PDF signature field; the
#: other four are text form fields the server fills from the signature record
#: (``services/attestation/pdf_signing.py``).
type PlacementFieldType = Literal[
    "signature", "initials", "name", "title", "date_signed"
]
type AppearanceKind = Literal["drawn", "typed"]
#: Every key ``models.attestation.TYPED_SIGNATURE_FONTS`` knows, script faces
#: first. The base-14 four stay in the contract even though the panel offers
#: whichever the deployment can actually stamp
#: (``AdoptedSignatureRead.available_fonts``): a mark adopted before the script
#: faces existed still has its key stored, and narrowing the type would turn
#: reading that officer's own signature back into a validation error.
type TypedSignatureFont = Literal[
    "caveat",
    "dancing_script",
    "great_vibes",
    "allura",
    "times_italic",
    "times_bold_italic",
    "helvetica_oblique",
    "courier_oblique",
]
type RecipientStatus = Literal["pending", "signed"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignerIdentityRead(ClosedModel):
    """The permanent signer identity. Opaque by design — it encodes nothing."""

    signer_id: str
    user_id: UUID
    display_name: str | None
    job_title: str | None
    provisioned_at: datetime
    has_active_key: bool


class SignatureSlotRead(ClosedModel):
    role: SigningRole
    min_count: int
    officer_titles: list[str]


class SigningPolicyRead(ClosedModel):
    source: str
    policy_id: str | None
    require_signature: bool
    require_signed_pdf: bool
    distinct_signers: bool
    required_attachments: list[str]
    required_signatures: list[SignatureSlotRead]


class OutstandingSlotRead(ClosedModel):
    role: SigningRole
    count: int


class CertificationPreviewRead(ClosedModel):
    """Exactly what the signer is shown before committing.

    The digest and the statement are both present because the UI must render
    what will actually be signed, not a paraphrase of it.
    """

    package_id: UUID
    return_code: str
    reporting_date: date
    basis: str
    attestation_state: AttestationState
    binding_class: BindingClass
    certification_digest: str
    content_digest: str
    register_state_digest: str | None
    signed_source_runs: list[dict[str, Any]]
    statement: str
    policy: SigningPolicyRead
    outstanding: list[OutstandingSlotRead]
    frozen_certification_digest: str | None
    matches_frozen: bool


class SignatureRead(ClosedModel):
    """A recorded signature. Everything needed to attribute it is here."""

    id: UUID
    signing_role: SigningRole
    officer_title: str | None
    signer_id: str
    signer_display_name: str | None
    binding_class: BindingClass
    certification_digest: str
    content_digest: str
    register_state_digest: str | None
    signed_source_runs: list[dict[str, Any]]
    statement: str
    signature_method: str
    certificate_sha256: str
    tsa_time: datetime | None
    tsa_url: str | None
    declared_at: datetime
    auth_evidence: dict[str, Any]
    attestation_cycle: int
    created_at: datetime


class SignatureRecipientRead(ClosedModel):
    """A named person routed to fill one policy slot."""

    id: UUID
    signing_role: SigningRole
    recipient_user_id: UUID
    recipient_signer_id: str
    recipient_display_name: str | None
    recipient_job_title: str | None
    routing_order: int
    status: RecipientStatus
    notified_at: datetime | None
    signed_at: datetime | None
    attestation_cycle: int


class AttestationStatusRead(ClosedModel):
    package_id: UUID
    attestation_state: AttestationState
    attestation_cycle: int
    certification_digest: str | None
    certified_at: datetime | None
    fully_certified_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None
    policy: SigningPolicyRead
    outstanding: list[OutstandingSlotRead]
    signatures: list[SignatureRead]
    recipients: list[SignatureRecipientRead]
    can_submit: bool


class StepUpRequest(ClosedModel):
    """Re-authentication before signing.

    Exactly one proof is required. Password re-entry serves password accounts;
    an OIDC id_token (obtained with prompt=login) serves SSO accounts.
    """

    signing_role: SigningRole
    id_token: str | None = None
    password: str | None = None


class StepUpGrantedRead(ClosedModel):
    """The single-use authorisation. The token is returned exactly once."""

    authorization_token: str
    expires_at: datetime
    certification_digest: str
    signing_role: SigningRole
    method: StepUpMethod | None


class CertifyRequest(ClosedModel):
    signing_role: SigningRole
    authorization_token: str
    #: The digest the client believed it was signing. Compared server-side, so a
    #: stale browser tab cannot certify figures the user never saw.
    expected_certification_digest: str


class VoidAttestationRequest(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)


class SendBackForCorrectionsRequest(ClosedModel):
    """The reviewing approver's note. Required, because it is the instruction.

    Sending a return back without saying what is wrong with it leaves the
    preparer guessing, so the note is not optional here even though a rejected
    approval decision accepts a longer free-text reason elsewhere.
    """

    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_substantive_note(self) -> SendBackForCorrectionsRequest:
        # min_length alone lets a space through, and a space is not an
        # instruction — the same reading PackageApprovalDecisionCreate takes.
        if not self.reason.strip():
            msg = "A note is required when sending a return back for corrections."
            raise ValueError(msg)
        return self


class PolicyUpsertRequest(ClosedModel):
    bank_id: str | None = None
    return_code: str | None = None
    return_family: str | None = Field(default=None, title="Policy scope return family")
    basis: Literal["solo", "consolidated"] | None = None
    required_signatures: list[SignatureSlotRead]
    required_attachments: list[str] = Field(default_factory=list)
    require_signature: bool = True
    require_signed_pdf: bool = False
    distinct_signers: bool = True
    effective_from: date
    effective_to: date | None = None
    reason: str = Field(min_length=1, max_length=500)


class PolicyRead(ClosedModel):
    id: UUID
    bank_id: str | None
    return_code: str | None
    # Explicitly titled: the generator names models after field titles, and a
    # bare "Return Family" would clobber the ReturnFamily enum the package
    # schemas depend on.
    return_family: str | None = Field(default=None, title="Policy return family")
    basis: str | None
    required_signatures: list[SignatureSlotRead]
    required_attachments: list[str]
    require_signature: bool
    require_signed_pdf: bool
    distinct_signers: bool
    effective_from: date
    effective_to: date | None
    reason: str
    updated_by: UUID | None
    updated_at: datetime


class PolicyListRead(ClosedModel):
    policies: list[PolicyRead]


class SignatureFieldPlacement(ClosedModel):
    """One placed box, in PDF user space (origin bottom-left).

    Coordinates are floats on the wire because a browser drag produces them, and
    are rounded to whole points server-side: sub-point precision in a signature
    box carries no meaning, and storing it would invite two placements that differ
    only in rounding.

    ``field_index`` is READ-ONLY in practice: the service renumbers each
    ``(role, kind)`` run from list order on every write, because the index exists
    only to tell two boxes of one kind apart and a client that had to invent it
    could collide them. It is on the model so the response can echo which field
    on the document a box became.
    """

    signing_role: PlaceableRole
    field_type: PlacementFieldType = "signature"
    field_index: int = Field(default=1, ge=1)
    page_index: int = Field(ge=0)
    x1: float
    y1: float
    x2: float
    y2: float


class PlacementFieldTypeRead(ClosedModel):
    """One placeable kind and the smallest box it can be drawn in.

    Served rather than hardcoded in the client because the floors are DERIVED —
    from the reference content each kind prints at the legibility floor — and a
    number duplicated in the browser would drift from the one that actually
    refuses a placement.
    """

    field_type: PlacementFieldType
    min_box_width: int
    min_box_height: int


class SignaturePlacementTemplateRead(ClosedModel):
    """The reusable placement set for one ``(bank?, return_code)`` scope."""

    bank_id: str | None
    return_code: str
    placements: list[SignatureFieldPlacement]
    reason: str
    updated_by: UUID | None
    updated_at: datetime


class SignaturePlacementTemplateListRead(ClosedModel):
    templates: list[SignaturePlacementTemplateRead]


class SignaturePlacementTemplateUpsertRequest(ClosedModel):
    """Replace the placement set for one scope. Reason-required and audited.

    The whole set is replaced rather than one role at a time because the two boxes
    are laid out relative to each other — a half-updated template could overlap
    without anyone having placed it that way.
    """

    bank_id: str | None = None
    return_code: str = Field(min_length=1, max_length=40)
    placements: list[SignatureFieldPlacement]
    reason: str = Field(min_length=1, max_length=500)


class ResolvedSignaturePlacementsRead(ClosedModel):
    """The placements this package's fields will actually be created from."""

    package_id: UUID
    return_code: str
    source: PlacementSource
    placements: list[SignatureFieldPlacement]
    #: Every placeable kind with its own floor. Replaces the single 185×61 that
    #: applied to signature fields regardless of what they had to print.
    field_types: list[PlacementFieldTypeRead]
    #: False once a signature exists: the fields are part of the certified
    #: revision and the DocMDP policy forbids adding or moving one afterwards.
    editable: bool


class PackageSignaturePlacementRequest(ClosedModel):
    """Place this package's fields. An empty list clears the override."""

    placements: list[SignatureFieldPlacement]
    reason: str = Field(min_length=1, max_length=500)


class AdoptedSignatureRead(ClosedModel):
    """The caller's adopted mark. ``image_png_base64`` is normalised PNG bytes."""

    adopted: bool
    signer_id: str
    kind: AppearanceKind | None
    image_png_base64: str | None
    image_width: int | None
    image_height: int | None
    typed_name: str | None
    typed_font: TypedSignatureFont | None
    adopted_at: datetime | None
    available_fonts: list[TypedSignatureFont]


class AdoptSignatureRequest(ClosedModel):
    """Adopt (or re-adopt) the caller's own mark.

    Exactly one kind's payload is required. Drawn bytes are re-rastered, stripped
    of metadata and re-encoded server-side before storage — the raw upload is
    never persisted.
    """

    kind: AppearanceKind
    image_png_base64: str | None = None
    typed_name: str | None = Field(default=None, max_length=120)
    typed_font: TypedSignatureFont | None = None


class SignatureRecipientNomination(ClosedModel):
    """One person put forward for one signing slot; list order is routing order."""

    signing_role: SigningRole
    user_id: UUID


class CertifyAndSendRequest(ClosedModel):
    """Certify as the preparer AND nominate the remaining signers, in one act.

    ``placements`` applies before the fields are created, so the preparer can
    place both boxes and sign in a single request — the only order that works,
    since the certification permits no new field afterwards. An EMPTY list means
    "leave placement as it resolves"; it does not clear an override, because
    clearing is a placement decision and belongs to the placement endpoint. It is
    a plain list rather than an optional one so the generated client keeps
    ``SignatureFieldPlacement`` as the element type — a nullable array degrades to
    an untyped one.
    """

    signing_role: SigningRole
    authorization_token: str
    expected_certification_digest: str
    recipients: list[SignatureRecipientNomination]
    placements: list[SignatureFieldPlacement] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)


class SignatureRoutingUpdateRequest(ClosedModel):
    """Re-assign the outstanding signatures — for an unavailable nominee."""

    recipients: list[SignatureRecipientNomination]
    reason: str = Field(min_length=1, max_length=500)


class AwaitingSignatureRead(ClosedModel):
    """One return waiting on the calling user's signature."""

    package_id: UUID
    bank_id: str
    return_code: str
    reporting_date: date
    basis: str
    package_version: int
    package_status: str
    attestation_state: AttestationState
    signing_role: SigningRole
    routing_order: int
    requested_at: datetime
    notified_at: datetime | None


class AwaitingSignatureListRead(ClosedModel):
    items: list[AwaitingSignatureRead]


class VerificationCheckRead(ClosedModel):
    """One of the five independent checks (docs §3.5)."""

    check: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class VerificationReportRead(ClosedModel):
    package_id: UUID
    return_code: str
    reporting_date: date
    verified_at: datetime
    overall_passed: bool
    checks: list[VerificationCheckRead]
    signatures: list[SignatureRead]
    chain_ok: bool
    chain_broken_at: str | None
