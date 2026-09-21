"""The Board signature slot: built, and off by default (D-043).

The bytes are proved in ``test_attestation_three_signer_chain.py``. This file
covers everything above them — which returns can carry a third block, who decides
whether they do, and the two service-layer refusals that stop a Board signature
arriving before the approver's.

The shape of the decision is worth stating once, because every test here is a
consequence of it. Nothing in the recovered BoG text requires the Board to
e-sign the filed PDF: ¶71 requires Board resolutions to ACCOMPANY the submission,
¶45 requires Board challenge and approval, Stress ¶20 requires a Board
attestation. So the default ICAAP policy is preparer + approver, exactly like
every other return, and a bank that wants the third signature turns the slot on
per return in Settings — an audited change. What the platform must guarantee is
that turning it on produces a document a verifier accepts, and that leaving it
off changes nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import IcaapSettings, get_settings
from app.models import (
    AttestationSignature,
    RegulatoryPackage,
    ReturnSigningPolicy,
    User,
)
from app.schemas.attestation import PolicyUpsertRequest, SignatureSlotRead
from app.services import attestation_api
from app.services.attestation import artifact_signing, layouts, pdf_signing, placements
from app.services.attestation.policy import (
    SignatureSlot,
    SigningPolicy,
    default_policy,
    resolve_policy,
)
from app.services.attestation.workflow import AttestationConflict, ensure_certifiable
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)
VAULT_KEY = "test-vault-master-key-not-for-production-0004"


@pytest.fixture(autouse=True)
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", VAULT_KEY)
    monkeypatch.setenv("SIGNING_SOFTWARE_KEY_DIR", str(tmp_path / "signing_keys"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _package(return_family: str, *, state: str = "preparer_certified") -> RegulatoryPackage:
    """A package instance for the pure guards — never added to a session.

    ``ensure_certifiable`` reads four attributes and writes none, so building the
    row in memory tests the guard rather than the fixture around it. It also lets
    a family be varied without a package of that family existing yet.
    """
    return RegulatoryPackage(
        id=uuid4(),
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_code="ICAAP-REPORT",
        return_family=return_family,
        reporting_date=REPORTING_DATE,
        basis="solo",
        status="validated",
        attestation_state=state,
        validation_report={"passed": True, "error_count": 0},
    )


def _error_code(exc: HTTPException) -> str:
    """The stable code out of a 422 body.

    ``HTTPException.detail`` is declared ``str`` upstream while every typed
    refusal in this codebase puts a ``{"error_code", "message"}`` dictionary in
    it, so the narrowing has to be written out rather than inferred.
    """
    detail = cast("object", exc.detail)
    assert isinstance(detail, dict)
    return str(cast("dict[str, object]", detail)["error_code"])


def _signature(role: str) -> AttestationSignature:
    return AttestationSignature(
        id=uuid4(),
        organization_id=DEMO_ORG_ID,
        package_id=uuid4(),
        signing_role=role,
        signer_id=f"SGN-{role.upper():<16.16}".replace(" ", "X"),
        signer_user_id=uuid4(),
    )


# --- what the default policy says -------------------------------------------


def test_the_icaap_default_has_no_board_slot() -> None:
    """D-043, at the only place it is actually decided.

    A bank that has configured nothing gets preparer + approver. The Board
    CAPABILITY exists — ``layouts`` will place a third block the moment a policy
    names one — but the platform never asks for a Board signature on its own.
    """
    policy = default_policy("icaap", "ICAAP-REPORT")
    assert [slot.role for slot in policy.slots] == ["preparer", "approver"]
    assert policy.slot_for("board") is None
    assert policy.require_signed_pdf
    assert policy.distinct_signers
    # …and the resulting ceremony is the two-signer one, so an ICAAP document
    # prepared under the default carries no dormant Sig_Board field.
    assert policy.field_roles("icaap") == ("preparer", "approver")


def test_no_other_family_changed() -> None:
    """Every non-ICAAP default is the one that was there before."""
    for family in ("liquidity", "capital", "irrbb", "bsd", "credit", "icaap_stress"):
        policy = default_policy(family)
        assert [slot.role for slot in policy.slots] == ["preparer", "approver"]
        assert policy.ordered_slots is False
        assert policy.field_roles(layouts.layout_for_family(family)) == (
            "preparer",
            "approver",
        )


def test_enabling_the_board_slot_adds_the_third_block() -> None:
    policy = SigningPolicy(
        slots=(
            SignatureSlot(role="preparer"),
            SignatureSlot(role="approver"),
            SignatureSlot(role="board"),
        ),
        ordered_slots=True,
    )
    assert policy.field_roles("icaap") == ("preparer", "approver", "board")
    # …but only on an artifact that has somewhere to put it. The same policy on
    # a BoG workbook return still signs with two, because that page is the
    # regulator's and we do not draw a third block onto it.
    assert policy.field_roles("standard") == ("preparer", "approver")


def test_order_is_forced_once_the_document_carries_three_fields() -> None:
    """An administrator cannot switch off a constraint the bytes impose.

    ``ordered_slots`` is a choice while there are two signature fields, because
    the preparer-then-approver order is already enforced by the attestation
    state machine. With three it stops being a choice: each signer's field lock
    seals the ones before it.
    """
    three = SigningPolicy(
        slots=(
            SignatureSlot(role="preparer"),
            SignatureSlot(role="approver"),
            SignatureSlot(role="board"),
        ),
        ordered_slots=False,
    )
    assert three.enforces_order("icaap") is True
    two = SigningPolicy(slots=(SignatureSlot("preparer"), SignatureSlot("approver")))
    assert two.enforces_order("standard") is False


def test_the_policy_payload_carries_the_order_flag() -> None:
    """``as_dict`` is what the certification dialog reads, so the flag must reach it."""
    assert default_policy("icaap").as_dict()["ordered_slots"] is True
    assert default_policy("liquidity").as_dict()["ordered_slots"] is False


# --- refusal layer 1: the workflow guard ------------------------------------


def test_the_board_cannot_certify_before_the_approver() -> None:
    """409 ``signing_order``, raised before the step-up token is consumed.

    ``signing.certify`` calls ``ensure_certifiable`` ahead of
    ``stepup.consume_authorization``, so a Board member who signs too early does
    not also lose the one-shot authorisation they just re-authenticated for.
    """
    policy = SigningPolicy(
        slots=(
            SignatureSlot("preparer"),
            SignatureSlot("approver"),
            SignatureSlot("board"),
        ),
        ordered_slots=True,
    )
    package = _package("icaap")
    with pytest.raises(AttestationConflict) as exc:
        ensure_certifiable(package, policy, "board", [_signature("preparer")])
    assert exc.value.error_code == "signing_order"
    assert "approver" in str(exc.value.detail)

    # With the approver's signature present it is the Board's turn.
    ensure_certifiable(package, policy, "board", [_signature("preparer"), _signature("approver")])


def test_the_order_guard_needs_the_signatures_it_judges() -> None:
    """A caller that omits them on an ordered return is a programming error.

    Silently skipping the check would be the worst outcome: the refusal that
    protects the Board's own signature would simply stop running, and nothing
    would look wrong until a verifier read the filed document.
    """
    policy = SigningPolicy(
        slots=(
            SignatureSlot("preparer"),
            SignatureSlot("approver"),
            SignatureSlot("board"),
        ),
        ordered_slots=True,
    )
    with pytest.raises(ValueError, match="current signatures"):
        ensure_certifiable(_package("icaap"), policy, "board")


def test_an_unordered_two_signer_return_never_consults_signatures() -> None:
    """The guard is inert for every return that existed before the Board slot."""
    policy = default_policy("liquidity")
    ensure_certifiable(_package("liquidity"), policy, "approver")


# --- refusal layer 2: what the dialog is told -------------------------------


def test_the_preview_names_who_must_sign_first(db_session: Session) -> None:
    """``blocked_by`` is why the dialog can disable the action with a reason.

    Asserted on a standard return, where it must always be empty: this is the
    plumbing check. The ordered case is covered by the workflow guard above and
    by the bytes layer, which are the two that refuse.
    """
    from app.services.attestation import signing  # noqa: PLC0415 - pulls the PDF stack

    package = _seed_standard_package(db_session)
    preview = signing.preview_certification(db_session, CTX, package, role="approver")
    assert preview["blocked_by"] == []
    assert preview["signing_order"] == ["preparer", "approver"]
    assert preview["policy"]["ordered_slots"] is False


# --- which artifact can carry which signature -------------------------------


def test_field_signing_roles_is_per_family() -> None:
    assert artifact_signing.field_signing_roles("liquidity") == {"preparer", "approver"}
    assert artifact_signing.field_signing_roles("bsd") == {"preparer", "approver"}
    assert artifact_signing.field_signing_roles("icaap") == {
        "preparer",
        "approver",
        "board",
    }
    # The constant the non-ICAAP world reads is unchanged.
    assert set(artifact_signing.FIELD_SIGNING_ROLES) == {"preparer", "approver"}


# --- ICAAP placements are fixed ---------------------------------------------


def test_icaap_placements_are_fixed_and_not_editable(db_session: Session) -> None:
    """The attestation page is ours, so there is nothing to accommodate.

    Every other family keeps the override → bank template → organization
    template → default chain untouched; this is the one layout where a stored
    placement could only move a signature off the rule it is drawn on.
    """
    materialize_canonical_test_book(db_session)
    package = _package("icaap", state="unsigned")

    two = placements.resolve(
        db_session, CTX, package, signing_order=("preparer", "approver")
    )
    assert two.source == "default"
    assert two.editable is False
    assert {p.signing_role for p in two.placements} == {"preparer", "approver"}

    three = placements.resolve(
        db_session, CTX, package, signing_order=("preparer", "approver", "board")
    )
    assert {p.signing_role for p in three.placements} == {
        "preparer",
        "approver",
        "board",
    }
    assert three.placements == pdf_signing.default_placements(
        ("preparer", "approver", "board"), layout="icaap"
    )

    # Omitting the order gives the two-signer default, never the layout's
    # maximum: a Sig_Board nobody may fill would block certification outright,
    # since a field cannot be removed once the preparer has certified.
    assert placements.resolve(db_session, CTX, package).placements == two.placements


def test_an_icaap_placement_cannot_be_overridden(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    package = _package("icaap", state="unsigned")
    boxes = pdf_signing.default_placements(("preparer", "approver"), layout="icaap")
    with pytest.raises(AttestationConflict) as exc:
        placements.set_package_override(
            db_session, CTX, package, placements=boxes, reason="move them"
        )
    assert exc.value.error_code == "placement_fixed_for_return"


def test_a_standard_return_is_still_placeable(db_session: Session) -> None:
    """The refusal above must not have widened to every return."""
    materialize_canonical_test_book(db_session)
    rows = placements.upsert_template(
        db_session,
        CTX,
        return_code="LCR-NSFR",
        bank_id=None,
        placements=(
            pdf_signing.FieldPlacement("preparer", 1, (51, 400, 291, 480)),
            pdf_signing.FieldPlacement("approver", 1, (304, 400, 544, 480)),
        ),
        reason="organization default",
    )
    assert len(rows) == 2


# --- what an administrator may save -----------------------------------------


def _policy_request(**overrides: object) -> PolicyUpsertRequest:
    payload: dict[str, object] = {
        "bank_id": SAMPLE_BANK_ID,
        "return_family": "icaap",
        "required_signatures": [
            SignatureSlotRead(role="preparer", min_count=1, officer_titles=[]),
            SignatureSlotRead(role="approver", min_count=1, officer_titles=[]),
            SignatureSlotRead(role="board", min_count=1, officer_titles=[]),
        ],
        "require_signed_pdf": True,
        "effective_from": date(2026, 1, 1),
        "reason": "Board signs the ICAAP report.",
    }
    payload.update(overrides)
    return PolicyUpsertRequest.model_validate(payload)


def test_a_board_policy_must_declare_its_order(db_session: Session) -> None:
    """Refused on save, not at certification time on a filing deadline."""
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as exc:
        attestation_api.upsert_policy(db_session, CTX, _policy_request())
    assert exc.value.status_code == 422
    assert _error_code(exc.value) == "ordered_slots_required"


def test_a_board_policy_with_an_order_is_accepted_and_round_trips(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    read = attestation_api.upsert_policy(
        db_session, CTX, _policy_request(ordered_slots=True)
    )
    assert read.ordered_slots is True
    assert [slot.role for slot in read.required_signatures] == [
        "preparer",
        "approver",
        "board",
    ]
    resolved = resolve_policy(
        db_session,
        CTX,
        bank_id=SAMPLE_BANK_ID,
        return_code="ICAAP-REPORT",
        return_family="icaap",
        basis="solo",
        as_at=REPORTING_DATE,
    )
    assert resolved.ordered_slots is True
    assert resolved.field_roles("icaap") == ("preparer", "approver", "board")


def test_a_board_signature_cannot_be_demanded_on_a_workbook_return(
    db_session: Session,
) -> None:
    """The refusal ``artifact_signing`` used to make at signing time, moved earlier.

    A BoG BSD form's attestation block is the regulator's own layout. We do not
    draw a third signing block onto it, so a policy asking for a Board signature
    ON that PDF is asking for something that cannot exist.
    """
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as exc:
        attestation_api.upsert_policy(
            db_session,
            CTX,
            _policy_request(return_family="bsd", ordered_slots=True),
        )
    assert exc.value.status_code == 422
    assert _error_code(exc.value) == "role_not_on_artifact"


def test_one_field_cannot_hold_two_signatures(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as exc:
        attestation_api.upsert_policy(
            db_session,
            CTX,
            _policy_request(
                ordered_slots=True,
                required_signatures=[
                    SignatureSlotRead(role="preparer", min_count=1, officer_titles=[]),
                    SignatureSlotRead(role="approver", min_count=2, officer_titles=[]),
                ],
            ),
        )
    assert exc.value.status_code == 422
    assert _error_code(exc.value) == "single_signature_per_field"


def test_a_policy_without_a_signed_pdf_may_name_any_role(db_session: Session) -> None:
    """The refusals above are about the ARTIFACT, so they only bind when there is one.

    A bank whose signing is the detached attestation alone can require a witness
    or a board signature as an institutional control; nothing is being attributed
    to a document that does not contain it.
    """
    materialize_canonical_test_book(db_session)
    read = attestation_api.upsert_policy(
        db_session,
        CTX,
        _policy_request(
            return_family="bsd",
            require_signed_pdf=False,
            required_signatures=[
                SignatureSlotRead(role="preparer", min_count=1, officer_titles=[]),
                SignatureSlotRead(role="approver", min_count=1, officer_titles=[]),
                SignatureSlotRead(role="witness", min_count=1, officer_titles=[]),
            ],
        ),
    )
    assert [slot.role for slot in read.required_signatures] == [
        "preparer",
        "approver",
        "witness",
    ]


# --- helpers ----------------------------------------------------------------


def _seed_standard_package(db: Session) -> RegulatoryPackage:
    """A real, validated LCR-NSFR package — the ordinary two-signer case."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import BankReportingPeriod  # noqa: PLC0415
    from app.schemas.regulatory_liquidity import RegulatoryRunCreate  # noqa: PLC0415
    from app.schemas.regulatory_reporting import RegulatoryPackageCreate  # noqa: PLC0415
    from app.services import regulatory_liquidity  # noqa: PLC0415
    from app.services.regulatory_reporting import generation, validation  # noqa: PLC0415

    materialize_canonical_test_book(db)
    db.add(
        ReturnSigningPolicy(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            return_code="LCR-NSFR",
            required_signatures=[
                {"role": "preparer", "min_count": 1, "officer_titles": []},
                {"role": "approver", "min_count": 1, "officer_titles": []},
            ],
            required_attachments=[],
            require_signature=True,
            require_signed_pdf=False,
            distinct_signers=True,
            effective_from=date(2026, 1, 1),
            reason="Two-signer baseline.",
        )
    )
    db.commit()
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    regulatory_liquidity.create_liquidity_run(
        db,
        CTX,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    read = generation.generate_package(
        db,
        CTX,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=REPORTING_DATE),
    )
    validation.validate_package(db, CTX, SAMPLE_BANK_ID, read.id)
    package = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert package is not None
    return package


# --- the deployment switch: ICAAP_SIGNING_ENABLED ----------------------------
#
# The founder's instruction: the ICAAP ceremony — Board slot included — is
# controlled from the environment the way the prudential returns' ceremony is
# controlled by ATTESTATION_SIGNING_ENABLED, so a Board that asks for the
# signature after onboarding is one variable away from it. It ships NO.
#
# The property every one of these tests exists to protect is that NO is DORMANT
# and not DELETED: a bank's configured three-signer policy has to come back
# byte for byte when the switch reads YES.


def _icaap_signing(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> None:
    monkeypatch.setenv("ICAAP_SIGNING_ENABLED", "YES" if on else "NO")
    get_settings.cache_clear()


def _resolve_icaap(db: Session) -> SigningPolicy:
    return resolve_policy(
        db,
        CTX,
        bank_id=SAMPLE_BANK_ID,
        return_code="ICAAP-REPORT",
        return_family="icaap",
        basis="solo",
        as_at=REPORTING_DATE,
    )


def test_the_icaap_signing_switch_ships_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO is the default, and ``YES``/``NO`` is a value the settings accept.

    The founder's words were ``YES|NO``. Pydantic parses those as a boolean, so
    the ``.env`` line a deployment actually writes is the one that works — and a
    test that only ever passed ``1``/``0`` would not have proved that.

    The default is asserted against NOTHING configured — no environment variable
    and no ``.env`` — because "ships off" is a claim about a deployment that has
    said nothing, and a developer who has switched it on locally must not be
    able to make that claim pass.
    """
    monkeypatch.delenv("ICAAP_SIGNING_ENABLED", raising=False)
    assert IcaapSettings(_env_file=None).signing_enabled is False  # type: ignore[call-arg]
    assert IcaapSettings(ICAAP_SIGNING_ENABLED="YES").signing_enabled is True  # type: ignore[call-arg]
    assert IcaapSettings(ICAAP_SIGNING_ENABLED="NO").signing_enabled is False  # type: ignore[call-arg]
    assert get_settings().icaap.signing_enabled is False


def test_the_switch_suspends_the_icaap_platform_default(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing configured at all, an ICAAP return demands no signature."""
    _icaap_signing(monkeypatch, on=False)
    resolved = _resolve_icaap(db_session)

    assert resolved.require_signature is False
    assert resolved.require_signed_pdf is False
    assert resolved.source == "icaap_signing_disabled"
    # Kept, and inert: the settings screen and the audit trail still show what
    # the ceremony WOULD be the moment the switch reads YES.
    assert [slot.role for slot in resolved.slots] == ["preparer", "approver"]


def test_the_switch_puts_a_configured_board_policy_dormant_and_gives_it_back(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one property that makes this a switch rather than a deletion.

    A bank configures the full three-signer ICAAP ceremony. The switch reads NO,
    the return takes the bare maker-checker path — and when it reads YES again
    the SAME row is in force, with its slots, its order and its id unchanged.
    Nothing about the bank's decision was lost while the deployment had the
    ceremony turned off.
    """
    materialize_canonical_test_book(db_session)
    _icaap_signing(monkeypatch, on=True)
    saved = attestation_api.upsert_policy(
        db_session, CTX, _policy_request(ordered_slots=True)
    )

    _icaap_signing(monkeypatch, on=False)
    dormant = _resolve_icaap(db_session)
    assert dormant.require_signature is False
    assert dormant.require_signed_pdf is False
    assert dormant.source == "icaap_signing_disabled"
    # Which row went dormant is recoverable, and so is everything it said.
    assert dormant.policy_id == str(saved.id)
    assert [slot.role for slot in dormant.slots] == ["preparer", "approver", "board"]
    assert dormant.ordered_slots is True

    _icaap_signing(monkeypatch, on=True)
    restored = _resolve_icaap(db_session)
    assert restored.require_signature is True
    assert restored.require_signed_pdf is True
    assert restored.source == "configured"
    assert restored.policy_id == str(saved.id)
    assert restored.ordered_slots is True
    assert restored.field_roles("icaap") == ("preparer", "approver", "board")


def test_the_switch_is_scoped_to_the_icaap_family_alone(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The difference between this and the deployment-wide kill-switch.

    ICAAP signing off must not quietly relax the prudential returns — those are
    governed by ``ATTESTATION_ESIGN_REQUIRED`` and by their own rows.
    """
    _icaap_signing(monkeypatch, on=False)
    resolved = resolve_policy(
        db_session,
        CTX,
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        basis="solo",
        as_at=REPORTING_DATE,
    )
    assert resolved.require_signature is True
    assert resolved.source == "platform_default"


def test_the_deployment_wide_kill_switch_outranks_the_icaap_one(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attribution names the switch that actually has to move.

    With ``ATTESTATION_ESIGN_REQUIRED=0`` every family is suspended, so turning
    ICAAP signing on alone would change nothing. Reporting ``icaap_signing_disabled``
    there would send an administrator to flip the switch that is not the cause.
    """
    _icaap_signing(monkeypatch, on=True)
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    resolved = _resolve_icaap(db_session)

    assert resolved.require_signature is False
    assert resolved.source == "esign_disabled"


def test_the_switch_does_not_misattribute_a_row_the_bank_itself_relaxed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bank that configured "no signature" was not overridden by a switch."""
    materialize_canonical_test_book(db_session)
    _icaap_signing(monkeypatch, on=True)
    attestation_api.upsert_policy(
        db_session,
        CTX,
        _policy_request(
            require_signature=False,
            require_signed_pdf=False,
            ordered_slots=True,
        ),
    )

    _icaap_signing(monkeypatch, on=False)
    resolved = _resolve_icaap(db_session)

    assert resolved.require_signature is False
    assert resolved.source == "configured"


def test_the_settings_surface_is_told_which_switch_is_suspending_signing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settings screen that listed the rows alone would state a requirement
    the platform is not applying. The switches ride on the same read."""
    materialize_canonical_test_book(db_session)

    _icaap_signing(monkeypatch, on=False)
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "1")
    get_settings.cache_clear()
    suspended = attestation_api.list_policies(db_session, CTX)
    assert suspended.icaap_signing_suspended is True
    assert suspended.signing_suspended_deployment_wide is False

    _icaap_signing(monkeypatch, on=True)
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()
    everything = attestation_api.list_policies(db_session, CTX)
    assert everything.icaap_signing_suspended is False
    assert everything.signing_suspended_deployment_wide is True


# --- reading the Board block back -------------------------------------------


def _seed_icaap_package(db: Session) -> tuple[RegulatoryPackage, TenantContext]:
    """A persisted ICAAP package, and the CAPITAL binding that may see it.

    ``icaap`` is a GATED family: the package does not exist for a principal
    holding no exact CAP/confidential binding for this institution, so the
    binding is part of the fixture rather than a detail of the assertion. The
    returned context carries the granted ``authorization_version``, which is
    what tells the evaluator it is looking at a human rather than a machine.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    from app.core.authorization import (  # noqa: PLC0415
        GrantorType,
        InstitutionScope,
        ModuleScope,
        PrincipalType,
        RoleBundle,
        SensitivityScope,
    )
    from app.services import authorization  # noqa: PLC0415

    materialize_canonical_test_book(db)
    authorization.create_role_binding(
        db,
        organization_id=DEMO_ORG_ID,
        principal_user_id=DEMO_USER_ID,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.APPROVER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.CAPITAL,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Read an ICAAP package's signature placements.",
    )
    package = RegulatoryPackage(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_family="icaap",
        return_code="ICAAP-REPORT",
        reporting_date=REPORTING_DATE,
        frequency="annual",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"sections": []},
        source_runs=[],
        generated_by=DEMO_USER_ID,
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    db.commit()
    principal = db.get(User, DEMO_USER_ID)
    assert principal is not None
    return package, TenantContext(
        organization_id=DEMO_ORG_ID,
        actor_user_id=DEMO_USER_ID,
        authorization_version=principal.authorization_version,
    )


def test_a_board_block_survives_the_read_contract(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Board slot has to be READABLE, or turning it on breaks the workspace.

    A client may only PLACE ``preparer`` and ``approver`` — a board placement is
    a 422 at the contract boundary and stays one. But the ICAAP attestation page
    is drawn by the platform, so a bank that has enabled the slot has a
    ``Sig_Board`` box on it, and the placements read is the request the signing
    workspace makes before it renders anything. Served through the write model,
    that response fails validation and the surface that makes the Board slot
    usable dies at the moment it is used.
    """
    package, ctx = _seed_icaap_package(db_session)
    _icaap_signing(monkeypatch, on=True)
    attestation_api.upsert_policy(db_session, CTX, _policy_request(ordered_slots=True))

    read = attestation_api.resolved_placements(
        db_session, ctx, SAMPLE_BANK_ID, package.id
    )

    assert "board" in {placement.signing_role for placement in read.placements}
    # The ceremony, in order — which is what the workspace labels its rail from.
    assert read.placeable_roles == ["preparer", "approver", "board"]
    # …and the boxes still cannot be MOVED: they sit on rules this platform drew.
    assert read.editable is False


def test_the_ceremony_is_reported_even_where_the_boxes_cannot_be_moved(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``editable`` answers "may these be dragged", not "who signs".

    They were one answer before: ``placeable_roles`` was emptied whenever the
    set was not editable, which is every ICAAP report. The workspace would then
    have had nothing to name its boxes or its recipient rail after, and an
    officer would have been shown a signing page that did not say who signs.
    """
    package, ctx = _seed_icaap_package(db_session)
    _icaap_signing(monkeypatch, on=True)

    read = attestation_api.resolved_placements(
        db_session, ctx, SAMPLE_BANK_ID, package.id
    )

    assert read.editable is False
    assert read.placeable_roles == ["preparer", "approver"]
