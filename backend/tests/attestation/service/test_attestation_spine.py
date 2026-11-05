"""The attestation spine: digests, identity, policy, guards, chain.

These tests pin the properties the whole capability rests on. They are
deliberately about BEHAVIOUR that would be a defect if it changed, not about
implementation shape.

This module's other-suite cases live in ``test_attestation_spine_integration.py``.
"""

from __future__ import annotations

__all__ = ["_package"]
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.attestation.service import digests, workflow
from app.attestation.service.identity import (
    SignerIdentityError,
    derive_signer_id,
)
from app.attestation.service.policy import (
    SignatureSlot,
    SigningPolicy,
    default_policy,
)
from app.core.config import get_settings
from app.models import (
    AttestationSignature,
    RegulatoryPackage,
)
from app.services.public_ids import is_signer_id
from tests.fixtures.canonical_bank_fixture import DEMO_USER_ID, SAMPLE_BANK_ID
from tests.support.helpers import ORG_1

PEPPER = "test-signer-pepper-not-production-0000"


@pytest.fixture(name="_pepper", autouse=True)
def signer_pepper(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", PEPPER)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _required_policy(*, approver_titles: tuple[str, ...] = ()) -> SigningPolicy:
    """A policy that an institution has opted into — signature mandatory.

    The guard tests must use this rather than ``default_policy``, because the
    unconfigured default deliberately requires no signature at all.
    """
    return SigningPolicy(
        slots=(
            SignatureSlot(role="preparer"),
            SignatureSlot(role="approver", officer_titles=approver_titles),
        ),
        require_signature=True,
    )


def test_content_digest_ignores_generated_at_but_not_figures() -> None:
    """The whole point of content_digest: identical figures, identical value.

    ``snapshot_sha256`` embeds ``metadata.generated_at`` and therefore changes
    on every regeneration (gap G13) — a signature bound to it could never be
    shown to cover "the same figures" across a re-render.
    """
    base = {
        "schema_version": "regulatory-package-v1",
        "sections": [{"code": "hqla", "rows": [{"line": "cash", "amount": "100.00"}]}],
        "metadata": {"generated_at": "2026-07-31T10:00:00+00:00", "baseline_run_id": "r1"},
    }
    later = {**base, "metadata": {**base["metadata"], "generated_at": "2026-08-01T09:30:00+00:00"}}
    changed = {
        **base,
        "sections": [{"code": "hqla", "rows": [{"line": "cash", "amount": "100.01"}]}],
    }

    assert digests.content_digest(base) == digests.content_digest(later)
    assert digests.content_digest(base) != digests.content_digest(changed)


def test_content_digest_does_not_mutate_the_snapshot() -> None:
    snapshot = {"metadata": {"generated_at": "x", "keep": "y"}}
    digests.content_digest(snapshot)
    assert snapshot["metadata"]["generated_at"] == "x"


def test_certification_digest_is_order_independent_over_source_runs() -> None:
    """Value-based, exactly like the engine input_hash: DB return order must not
    change what was signed."""
    runs_a = [
        {"module": "liquidity", "run_id": "r1", "input_hash": "h1", "engine_version": "v1"},
        {"module": "capital", "run_id": "r2", "input_hash": "h2", "engine_version": "v1"},
    ]
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": "p1",
        "package_version": 1,
        "return_code": "LCR-NSFR",
        "reporting_date": "2026-06-30",
        "basis": "solo",
        "content_digest_value": "c1",
        "binding_class": "engine_run",
    }
    assert digests.certification_digest(source_runs=runs_a, **common) == (
        digests.certification_digest(source_runs=list(reversed(runs_a)), **common)
    )


def test_certification_digest_changes_when_any_input_hash_changes() -> None:
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": "p1",
        "package_version": 1,
        "return_code": "LCR-NSFR",
        "reporting_date": "2026-06-30",
        "basis": "solo",
        "content_digest_value": "c1",
        "binding_class": "engine_run",
    }
    one = digests.certification_digest(
        source_runs=[{"module": "liquidity", "run_id": "r1", "input_hash": "h1"}], **common
    )
    two = digests.certification_digest(
        source_runs=[{"module": "liquidity", "run_id": "r1", "input_hash": "CHANGED"}], **common
    )
    assert one != two


def test_master_data_binding_requires_a_register_state_digest() -> None:
    """The LRT packs bind no engine run (gap G16). Refusing an empty binding is
    what stops a signature that proves nothing about provenance."""
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": "p1",
        "package_version": 1,
        "return_code": "LRT-PROFILE",
        "reporting_date": "2026-06-30",
        "basis": "solo",
        "content_digest_value": "c1",
        "binding_class": "master_data",
    }
    with pytest.raises(ValueError, match="register-state digest"):
        digests.certification_digest(**common)

    assert digests.certification_digest(register_state_digest_value="rs1", **common)


def test_engine_binding_refuses_to_masquerade_as_master_data() -> None:
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": "p1",
        "package_version": 1,
        "return_code": "LCR-NSFR",
        "reporting_date": "2026-06-30",
        "basis": "solo",
        "content_digest_value": "c1",
    }
    with pytest.raises(ValueError, match="at least one source run"):
        digests.certification_digest(binding_class="engine_run", **common)
    with pytest.raises(ValueError, match="must not carry source runs"):
        digests.certification_digest(
            binding_class="master_data",
            source_runs=[{"module": "liquidity", "run_id": "r", "input_hash": "h"}],
            register_state_digest_value="rs",
            **common,
        )


def test_canonicalisation_refuses_non_json_native_values() -> None:
    """No silent ``str()`` coercion: two different values must never hash the
    same because a ``default=`` handler flattened them."""
    with pytest.raises(digests.CanonicalisationError):
        digests.canonical_json({"amount": Decimal("1.00")})


def test_register_state_digest_tracks_values_not_row_order() -> None:
    state: digests.RegisterState = {
        "organization_id": "OR-TEST0001",
        "bank_id": "BK-TEST0001",
        "key_version": None,
        "rows": [
            {"table": "shareholdings", "id": "a", "updated_at": "t1", "values": {"pct": "50.00"}},
            {"table": "outlets", "id": "b", "updated_at": "t2", "values": {"city": "Accra"}},
        ],
    }
    assert digests.register_state_digest(state) == digests.register_state_digest(
        {**state, "rows": list(reversed(state["rows"]))}
    )
    mutated: digests.RegisterState = {
        **state,
        "rows": [{**state["rows"][0], "values": {"pct": "51.00"}}, state["rows"][1]],
    }
    assert digests.register_state_digest(state) != digests.register_state_digest(mutated)


def test_empty_register_commitment_binds_bank_scope_and_key_version() -> None:
    state: digests.RegisterState = {
        "organization_id": "OR-TEST0001",
        "bank_id": "BK-TEST0001",
        "key_version": "version-one",
        "rows": [],
    }
    sealed = digests.register_state_digest(state)
    assert sealed == digests.register_state_digest({**state})
    assert sealed != digests.register_state_digest({**state, "key_version": "version-two"})
    assert sealed != digests.register_state_digest({**state, "organization_id": "OR-TEST0002"})
    assert sealed != digests.register_state_digest({**state, "bank_id": "BK-TEST0002"})


def test_signer_id_is_deterministic_opaque_and_well_formed() -> None:
    user = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = derive_signer_id(user)
    assert first == derive_signer_id(user), "derivation must be deterministic"
    assert is_signer_id(first)
    # Opaque: the identifier must not leak the input it was derived from.
    assert str(user) not in first
    assert str(user).replace("-", "") not in first.lower()
    assert derive_signer_id(uuid4()) != first


def test_signer_id_collision_counter_changes_the_value() -> None:
    user = uuid4()
    assert derive_signer_id(user, collision_counter=1) != derive_signer_id(user)


def test_signer_id_requires_a_configured_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """A predictable signer id would let anyone holding a filed document
    enumerate platform user ids — so absent config fails loudly."""
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()
    with pytest.raises(SignerIdentityError, match="SIGNER_ID_PEPPER"):
        derive_signer_id(uuid4())
    get_settings.cache_clear()


def test_every_return_requires_two_signatures_out_of_the_box() -> None:
    """With no policy configured, attestation is REQUIRED — including DBK.

    A return leaves this platform only after the officer who prepared it and a
    second officer who checked it have signed the figures being filed. An
    earlier version defaulted this OFF on the grounds that BoG's requirements
    (C1-C4) are unconfirmed; that conflated what the regulator demands *of the
    artifact* with what the institution demands *of itself* before filing. Only
    the former is BoG's to answer, and none of it is hardcoded here.

    DBK is included deliberately despite being daily (10:00 T+1, five days a
    week, gap G17). The operational cost is real and is a bank's to accept or
    relax per return — not the platform's to waive silently.
    """
    for family in ("liquidity", "capital", "corporate", "dbk", "icaap_stress"):
        policy = default_policy(family)
        assert policy.require_signature is True, family
        assert policy.require_signed_pdf is True, family
        assert policy.distinct_signers is True, family
    assert [slot.role for slot in default_policy("liquidity").slots] == [
        "preparer",
        "approver",
    ]
    # Role-only: WHICH officer title must sign is C2, still unconfirmed, so
    # enforcing a guessed title would block a legitimate signer.
    assert all(not slot.officer_titles for slot in default_policy("liquidity").slots)


def test_officer_title_matching() -> None:
    role_only = SignatureSlot(role="approver")
    assert role_only.accepts_title(None) is True

    titled = SignatureSlot(role="approver", officer_titles=("Chief Financial Officer",))
    assert titled.accepts_title("Chief Financial Officer") is True
    assert titled.accepts_title("  chief financial officer  ") is True
    assert titled.accepts_title("Analyst") is False
    assert titled.accepts_title(None) is False


def _package(**overrides: object) -> RegulatoryPackage:
    defaults: dict[str, object] = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "return_family": "liquidity",
        "return_code": "LCR-NSFR",
        "reporting_date": date(2026, 6, 30),
        "frequency": "monthly",
        "basis": "solo",
        "status": "validated",
        "version": 1,
        "snapshot": {"sections": [], "metadata": {"generated_at": "t"}},
        "source_runs": [{"module": "liquidity", "run_id": "r1", "input_hash": "h1"}],
        "validation_report": {"passed": True, "error_count": 0},
        "generated_by": DEMO_USER_ID,
        "generated_at": datetime.now(UTC),
        "attestation_state": "unsigned",
        "attestation_cycle": 1,
        "register_state_digest": digests.register_state_digest(
            {"organization_id": ORG_1, "bank_id": SAMPLE_BANK_ID, "key_version": None, "rows": []}
        ),
    }
    defaults.update(overrides)
    return RegulatoryPackage(**defaults)


def test_compute_binding_classifies_by_source_runs() -> None:
    engine = workflow.compute_binding(_package(register_state_digest=None))
    assert engine.binding_class == "engine_run"

    master = _package(source_runs=[], register_state_digest="rs1", return_code="LRT-PROFILE")
    assert workflow.compute_binding(master).binding_class == "master_data"


def test_engine_binding_covers_register_and_engine_provenance_independently() -> None:
    package = _package(register_state_digest="register-before")
    original = workflow.compute_binding(package)
    assert original.binding_class == "engine_run"
    assert original.source_runs == package.source_runs
    assert original.register_state_digest == "register-before"
    package.register_state_digest = "register-after"
    assert workflow.compute_binding(package).certification_digest != original.certification_digest
    package.register_state_digest = "register-before"
    package.source_runs = [{**package.source_runs[0], "input_hash": "changed"}]
    assert workflow.compute_binding(package).certification_digest != original.certification_digest


def test_master_data_package_without_register_state_is_refused() -> None:
    package = _package(source_runs=[], register_state_digest=None, return_code="LRT-PROFILE")
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.compute_binding(package)
    assert excinfo.value.error_code == "register_state_missing"


def test_preparer_cannot_certify_an_unvalidated_package() -> None:
    policy = _required_policy()
    package = _package(status="generated")
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_certifiable(package, policy, "preparer")
    assert excinfo.value.error_code == "not_validated"


def test_preparer_cannot_certify_when_validation_has_errors() -> None:
    policy = _required_policy()
    package = _package(validation_report={"passed": False, "error_count": 3})
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_certifiable(package, policy, "preparer")
    assert excinfo.value.error_code == "validation_not_clean"


def test_approver_cannot_certify_before_the_preparer() -> None:
    policy = _required_policy()
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_certifiable(_package(), policy, "approver")
    assert excinfo.value.error_code == "preparer_certification_missing"


def test_role_outside_the_policy_is_refused() -> None:
    policy = _required_policy()
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_certifiable(_package(), policy, "board")
    assert excinfo.value.error_code == "role_not_in_policy"


def _signature(**overrides: object) -> AttestationSignature:
    defaults: dict[str, object] = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": uuid4(),
        "package_version": 1,
        "signing_role": "preparer",
        "signer_id": "SGN-AAAAAAAAAAAAAAAA",
        "signer_user_id": DEMO_USER_ID,
        "binding_class": "engine_run",
        "certification_digest": "d" * 64,
        "content_digest": "c" * 64,
        "statement": "I certify…",
        "attestation_payload": {"schema": "aequoros-signature-v1"},
        "payload_digest": "p" * 64,
        "signature_method": "detached_ecdsa_p256_sha256",
        "signature_value": b"sig",
        "certificate_pem": "-----BEGIN CERTIFICATE-----",
        "certificate_sha256": "f" * 64,
        "declared_at": datetime.now(UTC),
        "prev_hash": workflow.GENESIS_HASH,
        "entry_hash": "e" * 64,
        "attestation_cycle": 1,
    }
    defaults.update(overrides)
    return AttestationSignature(**defaults)


def test_maker_checker_blocks_the_generator_from_approving() -> None:
    policy = _required_policy()
    package = _package(attestation_state="preparer_certified", status="pending_approval")
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_maker_checker(
            package,
            policy,
            [],
            role="approver",
            user_id=DEMO_USER_ID,  # same person who generated it
            signer_id="SGN-BBBBBBBBBBBBBBBB",
            job_title=None,
        )
    assert excinfo.value.error_code == "maker_checker"


def test_maker_checker_blocks_the_same_signer_twice() -> None:
    policy = _required_policy()
    other = uuid4()
    package = _package(
        generated_by=uuid4(), attestation_state="preparer_certified", status="pending_approval"
    )
    existing = _signature(signer_user_id=other, signer_id="SGN-CCCCCCCCCCCCCCCC")
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_maker_checker(
            package,
            policy,
            [existing],
            role="approver",
            user_id=other,
            signer_id="SGN-CCCCCCCCCCCCCCCC",
            job_title=None,
        )
    assert excinfo.value.error_code == "maker_checker"


def test_maker_checker_catches_a_reprovisioned_signer_id() -> None:
    """Comparing on the permanent signer id as well as the user id is what
    catches the same human returning under a new user row."""
    policy = _required_policy()
    package = _package(
        generated_by=uuid4(), attestation_state="preparer_certified", status="pending_approval"
    )
    existing = _signature(signer_user_id=uuid4(), signer_id="SGN-DDDDDDDDDDDDDDDD")
    with pytest.raises(workflow.AttestationConflict):
        workflow.ensure_maker_checker(
            package,
            policy,
            [existing],
            role="approver",
            user_id=uuid4(),
            signer_id="SGN-DDDDDDDDDDDDDDDD",
            job_title=None,
        )


def test_officer_title_mismatch_is_refused() -> None:
    policy = _required_policy(approver_titles=("Chief Financial Officer",))
    package = _package(
        generated_by=uuid4(), attestation_state="preparer_certified", status="pending_approval"
    )
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_maker_checker(
            package,
            policy,
            [],
            role="approver",
            user_id=uuid4(),
            signer_id="SGN-EEEEEEEEEEEEEEEE",
            job_title="Analyst",
        )
    assert excinfo.value.error_code == "officer_title_mismatch"


def test_digest_drift_after_certification_is_refused() -> None:
    """The single most important guard: the approver must be signing the
    identical figures the preparer certified."""
    package = _package(certification_digest="frozen" + "0" * 58)
    binding = workflow.compute_binding(package)
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_digest_unchanged(package, binding)
    assert excinfo.value.error_code == "figures_changed_since_certification"


def test_outstanding_slots_and_full_certification() -> None:
    policy = _required_policy()
    assert workflow.outstanding_slots(policy, []) == [("preparer", 1), ("approver", 1)]

    preparer = _signature(signing_role="preparer")
    assert workflow.outstanding_slots(policy, [preparer]) == [("approver", 1)]
    assert workflow.is_fully_certified(policy, [preparer]) is False

    approver = _signature(signing_role="approver")
    assert workflow.is_fully_certified(policy, [preparer, approver]) is True


def test_chain_entry_hash_is_sensitive_to_every_input() -> None:
    base = workflow.chain_entry_hash(
        prev_hash=workflow.GENESIS_HASH, payload_digest="a" * 64, signature_value=b"sig"
    )
    assert base != workflow.chain_entry_hash(
        prev_hash="b" * 64, payload_digest="a" * 64, signature_value=b"sig"
    )
    assert base != workflow.chain_entry_hash(
        prev_hash=workflow.GENESIS_HASH, payload_digest="c" * 64, signature_value=b"sig"
    )
    assert base != workflow.chain_entry_hash(
        prev_hash=workflow.GENESIS_HASH, payload_digest="a" * 64, signature_value=b"other"
    )
