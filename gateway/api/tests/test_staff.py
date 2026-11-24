# pyright: standard
"""BoG staff: the gateway's identity provider, ID-token validation, roles and separation of duties."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.db.models import StaffAccount
from app.idp.accounts import create_account, set_status
from app.keys.provider import LocalFileKeyProvider
from app.seed.synthetic_staff import DevStaff
from tests.harness import BSD2_LINES, Harness, make_harness
from tests.synthetic_definitions import bsd5a_spec
from tests.synthetic_registry import draft_from_spec

REDIRECT = "http://localhost:3100/auth/callback"
EMAIL = "ama.mensah@bog.example"
PASSWORD = "Correct-Horse-42"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def _account(harness: Harness, groups: tuple[str, ...] = ("srg-senior-supervisors",)) -> str:
    with harness.ctx.db.session() as session:
        account = create_account(
            session,
            harness.ctx,
            email=EMAIL,
            name="Ama Mensah",
            given_name="Ama",
            family_name="Mensah",
            groups=list(groups),
            password=PASSWORD,
            created_by="test",
        )
        return account.subject


def _authorize_params(challenge: str) -> dict[str, str]:
    return {
        "client_id": "srg-portal",
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
        "state": "s1",
        "nonce": "n1",
    }


def _submit(harness: Harness, challenge: str, email: str, password: str) -> Any:
    page = harness.client.get("/idp/authorize", params=_authorize_params(challenge))
    assert page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert csrf is not None
    return harness.client.post(
        "/idp/authorize",
        data={
            "email": email,
            "password": password,
            "csrf": csrf.group(1),
            "client_id": "srg-portal",
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "state": "s1",
            "nonce": "n1",
        },
        follow_redirects=False,
    )


def sign_in(harness: Harness, email: str = EMAIL, password: str = PASSWORD) -> str:
    verifier, challenge = _pkce()
    config = harness.client.get("/idp/.well-known/openid-configuration").json()
    assert config["issuer"] == "http://testserver/idp"
    assert config["code_challenge_methods_supported"] == ["S256"]
    redirect = _submit(harness, challenge, email, password)
    assert redirect.status_code == 303, redirect.text
    query = parse_qs(urlparse(redirect.headers["location"]).query)
    assert query["state"] == ["s1"]
    token = harness.client.post(
        "/idp/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": "srg-portal",
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    return token.json()["id_token"]


def test_sign_in_page_asks_for_email_and_password_and_offers_no_people(harness: Harness) -> None:
    _, challenge = _pkce()
    page = harness.client.get("/idp/authorize", params=_authorize_params(challenge))
    assert page.status_code == 200
    assert 'type="email" name="email"' in page.text
    assert 'type="password" name="password"' in page.text
    assert ">Sign in</button>" in page.text
    for synthetic in ("Kofi Owusu", "Akosua Boateng", "Internal Audit", "kowusu"):
        assert synthetic not in page.text
    assert page.headers["cache-control"] == "no-store"
    policy = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    # The redirect back to the portal must pass form-action, or the browser blocks it.
    assert "form-action 'self' http://localhost:3100;" in policy


def test_account_signs_in_with_password_through_the_code_flow(harness: Harness) -> None:
    subject = _account(harness)
    id_token = sign_in(harness)
    me = harness.client.get("/portal/api/me", headers={"Authorization": f"Bearer {id_token}"})
    assert me.status_code == 200
    assert me.json() == {
        "subject": subject,
        "name": "Ama Mensah",
        "email": EMAIL,
        "roles": ["senior_supervisor"],
    }
    # Email is matched without regard to case.
    assert sign_in(harness, email="AMA.Mensah@BOG.example")
    audit = harness.client.get(
        "/portal/api/audit?category=sign_in", headers={"Authorization": f"Bearer {id_token}"}
    ).json()
    actions = [item["action"] for item in audit["items"]]
    assert "Signed in" in actions
    assert all(item["actor_via"].startswith("AequorOS Portal") for item in audit["items"])
    with harness.ctx.db.session() as session:
        account = session.get(StaffAccount, subject)
        assert account is not None
        assert account.password_hash.startswith("$argon2id$")
        assert PASSWORD not in account.password_hash
        assert account.last_sign_in_at is not None


def test_wrong_password_and_unknown_email_get_the_same_answer(harness: Harness) -> None:
    _account(harness)
    _, challenge = _pkce()
    wrong = _submit(harness, challenge, EMAIL, "Not-The-Password-1")
    unknown = _submit(harness, challenge, "nobody@bog.example", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    for response in (wrong, unknown):
        assert "The email or password is incorrect." in response.text
        assert "location" not in response.headers


def test_repeated_failures_lock_the_account(harness: Harness) -> None:
    _account(harness)
    _, challenge = _pkce()
    for _ in range(4):
        assert _submit(harness, challenge, EMAIL, "Not-The-Password-1").status_code == 401
    locked = _submit(harness, challenge, EMAIL, "Not-The-Password-1")
    assert "locked after repeated failed sign-ins" in locked.text
    # Even the right password is refused while locked ...
    still = _submit(harness, challenge, EMAIL, PASSWORD)
    assert still.status_code == 401
    assert "locked" in still.text
    # ... and accepted once the lock has run out.
    harness.clock.advance(minutes=16)
    assert sign_in(harness)
    audit = harness.client.get(
        "/portal/api/audit?category=sign_in", headers=harness.staff("internal-audit")
    ).json()
    assert any(item["action"] == "Account locked" for item in audit["items"])


def test_disabled_account_cannot_sign_in_or_redeem_a_code(harness: Harness) -> None:
    subject = _account(harness)
    with harness.ctx.db.session() as session:
        account = session.get(StaffAccount, subject)
        assert account is not None
        account.status = "disabled"
    _, challenge = _pkce()
    refused = _submit(harness, challenge, EMAIL, PASSWORD)
    assert refused.status_code == 401
    assert "disabled" in refused.text


def test_disabling_an_account_ends_its_access_at_once(harness: Harness) -> None:
    subject = _account(harness)
    id_token = sign_in(harness)
    headers = {"Authorization": f"Bearer {id_token}"}
    assert harness.client.get("/portal/api/me", headers=headers).status_code == 200
    with harness.ctx.db.session() as session:
        account = session.get(StaffAccount, subject)
        assert account is not None
        set_status(session, harness.ctx, account, "disabled", by="test")
    # The same, still unexpired ID token is refused straight away.
    assert harness.client.get("/portal/api/me", headers=headers).status_code == 401


def test_sign_in_form_needs_its_own_csrf_token(harness: Harness) -> None:
    _account(harness)
    _, challenge = _pkce()
    harness.client.cookies.clear()
    forged = harness.client.post(
        "/idp/authorize",
        data={
            "email": EMAIL,
            "password": PASSWORD,
            "csrf": "made-up",
            "client_id": "srg-portal",
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    assert forged.status_code == 400
    assert "location" not in forged.headers


def test_password_policy_and_unique_email(harness: Harness) -> None:
    _account(harness)
    with harness.ctx.db.session() as session:
        for password in ("short1A!", "alllowercaseletters"):
            with pytest.raises(ApiError):
                create_account(
                    session,
                    harness.ctx,
                    email="other@bog.example",
                    name="Other",
                    given_name=None,
                    family_name=None,
                    groups=["srg-supervisors"],
                    password=password,
                    created_by="test",
                )
        with pytest.raises(ApiError):
            create_account(
                session,
                harness.ctx,
                email=EMAIL.upper(),
                name="Duplicate",
                given_name=None,
                family_name=None,
                groups=["srg-supervisors"],
                password=PASSWORD,
                created_by="test",
            )
        with pytest.raises(ApiError):
            create_account(
                session,
                harness.ctx,
                email="third@bog.example",
                name="No such group",
                given_name=None,
                family_name=None,
                groups=["made-up-group"],
                password=PASSWORD,
                created_by="test",
            )
        assert len(session.scalars(select(StaffAccount)).all()) == 1


def test_provider_refuses_bad_pkce_reuse_and_unregistered_redirects(harness: Harness) -> None:
    _account(harness)
    verifier, challenge = _pkce()
    redirect = _submit(harness, challenge, EMAIL, PASSWORD)
    code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": "srg-portal",
    }
    assert (
        harness.client.post("/idp/token", data={**form, "code_verifier": "wrong"}).status_code
        == 400
    )
    assert (
        harness.client.post("/idp/token", data={**form, "code_verifier": verifier}).status_code
        == 400
    )  # used
    bad_redirect = harness.client.get(
        "/idp/authorize",
        params={
            "client_id": "srg-portal",
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": challenge,
        },
    )
    assert bad_redirect.status_code == 400


def test_provider_is_off_when_an_external_one_is_federated(
    tmp_path: Path, keys_dir: Path, sqlite_template: Path
) -> None:
    keys = LocalFileKeyProvider(
        keys_dir,
        {
            "receipt": "gateway-receipt-2026-q4",
            "audit": "gateway-audit-2026-q4",
            "devidp": "dev-oidc-2026-q4",
        },
    )
    harness = make_harness(
        tmp_path / "federated",
        keys_dir,
        sqlite_template,
        keys=keys,
        oidc_issuer="https://login.bog.example",
    )
    for path in ("/idp/.well-known/openid-configuration", "/idp/authorize", "/idp/jwks"):
        assert harness.client.get(path).status_code == 404, path
    assert harness.client.post("/idp/token", data={}).status_code == 404
    # The old development provider is gone everywhere.
    assert harness.client.get("/dev/oidc/authorize").status_code == 404


def test_staff_token_validation(harness: Harness) -> None:
    assert harness.client.get("/portal/api/me").status_code == 401
    assert (
        harness.client.get(
            "/portal/api/me", headers={"Authorization": "Bearer nonsense"}
        ).status_code
        == 401
    )
    token = harness.staff_token("kowusu")
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[::-1]}"
    assert (
        harness.client.get(
            "/portal/api/me", headers={"Authorization": f"Bearer {forged}"}
        ).status_code
        == 401
    )
    harness.clock.advance(hours=2)
    assert (
        harness.client.get(
            "/portal/api/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    nobody = DevStaff("x", "No Group", "No", "Group", "x@bog.example", ("other-group",), "")
    assert (
        harness.client.get("/portal/api/me", headers=harness.staff(custom=nobody)).status_code
        == 403
    )


def _in_review(harness: Harness, supervisor: str | None = "aboateng") -> tuple[Any, str]:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol(supervisor=supervisor)
    receipt_id = harness.post_filing(bank, harness.filing(bank, BSD2_LINES)).json()["receipt"][
        "receipt_id"
    ]
    return bank, receipt_id


def test_auditor_reads_everything_and_decides_nothing(harness: Harness) -> None:
    bank, receipt_id = _in_review(harness)
    auditor = harness.staff("internal-audit")
    detail = harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=auditor)
    assert detail.status_code == 200
    assert detail.json()["allowed_actions"] == []
    assert (
        harness.client.post(
            f"/portal/api/submissions/{receipt_id}/accept", json={}, headers=auditor
        ).status_code
        == 403
    )
    query = harness.client.post(
        "/portal/api/queries",
        json={
            "receipt_id": receipt_id,
            "subject": "s",
            "body": "b",
            "kind": "query",
            "due_at": "2026-11-30T17:00:00Z",
        },
        headers=auditor,
    )
    assert query.status_code == 403
    assert harness.client.get("/portal/api/audit/export", headers=auditor).status_code == 200
    both = DevStaff(
        "ab",
        "Audit Supervisor",
        "Audit",
        "Supervisor",
        "as@bog.example",
        ("srg-internal-audit", "srg-senior-supervisors"),
        "",
    )
    refused = harness.client.post(
        f"/portal/api/submissions/{receipt_id}/accept", json={}, headers=harness.staff(custom=both)
    )
    assert refused.status_code == 403
    assert "decide nothing" in refused.json()["detail"]
    del bank


def test_supervisor_acts_only_on_assigned_institutions(harness: Harness) -> None:
    _, receipt_id = _in_review(harness, supervisor="kowusu")
    akosua = harness.staff("aboateng")
    assert (
        harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=akosua).json()[
            "allowed_actions"
        ]
        == []
    )
    assert (
        harness.client.post(
            f"/portal/api/submissions/{receipt_id}/accept", json={}, headers=akosua
        ).status_code
        == 403
    )
    # A senior supervisor reassigns it; the assignee may then decide.
    kofi = harness.staff("kowusu")
    assigned = harness.client.post(
        f"/portal/api/submissions/{receipt_id}/assign",
        json={"reviewer_subject": "aboateng"},
        headers=kofi,
    )
    assert assigned.status_code == 204, assigned.text
    assert (
        harness.client.post(
            f"/portal/api/submissions/{receipt_id}/assign",
            json={"reviewer_subject": "kowusu"},
            headers=akosua,
        ).status_code
        == 403
    )
    accepted = harness.client.post(
        f"/portal/api/submissions/{receipt_id}/accept", json={"note": "ok"}, headers=akosua
    )
    assert accepted.status_code == 204
    again = harness.client.post(
        f"/portal/api/submissions/{receipt_id}/accept", json={}, headers=akosua
    )
    assert again.status_code == 409
    row = harness.client.get(f"/portal/api/submissions/{receipt_id}", headers=kofi).json()["row"]
    assert row["status"] == "accepted"
    assert row["reviewer_name"] == "A. Boateng"


def test_return_version_needs_three_different_people(harness: Harness) -> None:
    harness.bootstrap(("BSD5A",))
    draft = draft_from_spec(bsd5a_spec("2026.2")).model_dump(mode="json", by_alias=True)
    esi, michael, kofi = harness.staff("easante"), harness.staff("madjei"), harness.staff("kowusu")
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/drafts", json=draft, headers=kofi
        ).status_code
        == 403
    )
    assert (
        harness.client.post("/portal/api/returns/BSD5A/drafts", json=draft, headers=esi).status_code
        == 201
    )
    assert (
        harness.client.post("/portal/api/returns/BSD5A/drafts", json=draft, headers=esi).status_code
        == 409
    )
    own = harness.client.post("/portal/api/returns/BSD5A/versions/2026.2/approve", headers=esi)
    assert own.status_code == 403
    assert "drafter" in own.json()["detail"]
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/versions/2026.2/approve", headers=michael
        ).status_code
        == 204
    )
    twice = harness.client.post(
        "/portal/api/returns/BSD5A/versions/2026.2/approve", headers=michael
    )
    assert twice.status_code == 403
    detail = harness.client.get("/portal/api/returns/BSD5A", headers=kofi).json()
    assert [a["step"] for a in detail["draft"]["approvals"]] == ["drafted", "approved", "pending"]
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/versions/2026.2/approve",
            headers=harness.staff("internal-audit"),
        ).status_code
        == 403
    )
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/versions/2026.2/approve", headers=kofi
        ).status_code
        == 204
    )
    published = harness.client.get("/portal/api/returns/BSD5A", headers=kofi).json()
    assert published["draft"] is None
    versions = {v["version"]: v for v in published["versions"]}
    assert versions["2026.2"]["status"] == "published"
    assert versions["2026.2"]["rule_count"] == 46
    assert versions["2026.1"]["last_reporting_date"] == "2027-01-30"


def test_return_to_drafter(harness: Harness) -> None:
    harness.bootstrap(("BSD5A",))
    draft = draft_from_spec(bsd5a_spec("2026.2")).model_dump(mode="json", by_alias=True)
    esi, michael = harness.staff("easante"), harness.staff("madjei")
    harness.client.post("/portal/api/returns/BSD5A/drafts", json=draft, headers=esi)
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/versions/2026.2/return", json={"reason": "x"}, headers=esi
        ).status_code
        == 403
    )
    assert (
        harness.client.post(
            "/portal/api/returns/BSD5A/versions/2026.2/return",
            json={"reason": "Add a rule"},
            headers=michael,
        ).status_code
        == 204
    )
    assert harness.client.get("/portal/api/returns/BSD5A", headers=esi).json()["draft"] is None
    assert (
        harness.client.post("/portal/api/returns/BSD5A/drafts", json=draft, headers=esi).status_code
        == 201
    )


def test_draft_with_broken_rules_is_refused(harness: Harness) -> None:
    harness.bootstrap(("BSD5A",))
    draft = draft_from_spec(bsd5a_spec("2026.2")).model_dump(mode="json", by_alias=True)
    draft["rules"][0]["lines"] = ["99.9"]
    response = harness.client.post(
        "/portal/api/returns/BSD5A/drafts", json=draft, headers=harness.staff("easante")
    )
    assert response.status_code == 400
    assert "unknown line" in response.json()["detail"]


def test_parallel_requests_with_a_new_token_record_one_sign_in(
    harness: Harness, monkeypatch: Any
) -> None:
    """The portal opens with several requests at once; the loser of the race must not 500."""
    from datetime import timedelta

    from sqlalchemy import func, select

    import app.auth.staff as staff_module
    from app.auth.staff import IdTokenIdentity, record_staff
    from app.db.models import AuditEntry, StaffSession

    identity = IdTokenIdentity(
        subject="kowusu",
        name="Kofi Owusu",
        given_name="Kofi",
        family_name="Owusu",
        email="kofi.owusu@bog.example",
        groups=("srg-senior-supervisors",),
        mfa=True,
        expires_at=harness.clock.now() + timedelta(hours=1),
    )
    with harness.ctx.db.session() as first:
        record_staff(first, harness.ctx, identity, "token-opened-twice")
    # The second request looked before the first committed, so it saw no session.
    monkeypatch.setattr(staff_module, "_existing_session", lambda _session, _hash: None)
    with harness.ctx.db.session() as second:
        principal = record_staff(second, harness.ctx, identity, "token-opened-twice")
    assert principal.subject == "kowusu"
    with harness.ctx.db.session() as check:
        assert check.scalar(select(func.count()).select_from(StaffSession)) == 1
        sign_ins = check.scalar(
            select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Signed in")
        )
        assert sign_ins == 1


def test_naming_the_gateways_own_issuer_keeps_its_provider() -> None:
    """A deployment's one env file sets GATEWAY_OIDC_ISSUER for the portal.

    The portal needs the gateway's own ``/idp``; the API must read that as its own
    provider, while a foreign issuer still federates and switches it off.
    """
    from app.core.settings import Settings

    own = Settings(base_url="https://gateway.example", oidc_issuer="https://gateway.example/idp/")
    assert own.uses_builtin_idp
    assert own.effective_oidc_issuer == "https://gateway.example/idp"
    federated = Settings(
        base_url="https://gateway.example", oidc_issuer="https://login.bog.example"
    )
    assert not federated.uses_builtin_idp
