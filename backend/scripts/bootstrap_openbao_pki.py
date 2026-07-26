#!/usr/bin/env python
"""Provision the OpenBao PKI that certifies officers' signing keys.

``SIGNING_BACKEND=openbao`` issues every officer certificate from a PKI mount
(``app/services/attestation/openbao.py``, docs/attestation_esignature.md §3.4).
None of that exists on a fresh server, and none of it can be created by the
application's AppRole — mounting an engine and generating a root are
administrator acts. So this script is the operational half of the feature, run
once per environment by whoever holds an admin token::

    BAO_TOKEN=<admin token> uv run python scripts/bootstrap_openbao_pki.py \\
        --addr https://bao.internal:8200 \\
        --organization "Sample Bank" --country GH \\
        --public-addr https://bao.internal:8200 \\
        --trust-roots /etc/aequoros/trust-roots

What it creates, and why each piece is not optional:

* a **root** mount (``pki``) holding a long-lived self-signed CA. This is the
  anchor an examiner is handed once; it signs nothing but the intermediate.
* an **issuing** mount (``pki-int``) whose intermediate is signed by the root.
  Officer certificates come from here, so a compromise or a rotation replaces
  one intermediate rather than the institution's root.
* a **role** (``aequoros-signer``) pinning ``digitalSignature`` +
  ``nonRepudiation``, a bounded lifetime, and ``allowed_serial_numbers=SGN-*``.
  The role is what makes the CA — not the application — decide what may be
  issued: a compromised application cannot mint itself a CA certificate, a
  40-year key, or a certificate whose subject identifier is not a platform
  signer id.
* **CRL and OCSP URLs** on both mounts. Revocation that nobody can fetch is not
  revocation, and PAdES B-LTA collects exactly this material at signing time so
  a filed return stays verifiable after the CA is gone.
* an **ACL policy** for the application's AppRole, covering the Transit paths it
  signs with and the PKI paths it issues and revokes on. Without the revoke
  capability a departed officer's certificate would keep verifying.
* the **trust anchor file** that ``ATTESTATION_TRUST_ROOTS`` points at, written
  from the root the issuing mount actually chains to (``--trust-roots``). This
  is the one manual link in the chain, so the script does it rather than leaving
  an operator to copy PEM between two places and believe roots are in force when
  they are not.

Idempotent: re-running against a provisioned server re-applies the role, the
URLs and the policy, and leaves existing mounts and CAs alone. It never
regenerates a root — that would orphan every certificate already issued.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from cryptography import x509

# The application's own view of the mount, so the script and the runtime cannot
# disagree about which certificate is "the root".
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.attestation.openbao import (  # noqa: E402
    KEY_NAME_PREFIX,
    SignerBackendError,
    certificates_to_pem,
    self_signed_anchor,
)

#: Mount and role defaults, matching ``AttestationSettings``. Changing one here
#: means changing OPENBAO_PKI_MOUNT / OPENBAO_PKI_ROLE in the deployment too.
DEFAULT_ROOT_MOUNT = "pki"
DEFAULT_ISSUING_MOUNT = "pki-int"
DEFAULT_ROLE = "aequoros-signer"
DEFAULT_POLICY = "aequoros-signer"

#: Root 10 years, intermediate 5, leaf 1. The root outlives every certificate
#: beneath it, so nothing an officer signed is orphaned by an expiry an operator
#: forgot; the leaf is short because a signature's long-term validity comes from
#: its RFC 3161 timestamp, not from a certificate that never expires.
ROOT_TTL = "87600h"
INTERMEDIATE_TTL = "43800h"
LEAF_MAX_TTL = "8760h"

#: The certificate profile. ``nonRepudiation`` (OpenBao spells it
#: ``ContentCommitment``) is the whole point of an attestation key and is what
#: pyHanko's default signer key-usage constraint looks for; ``server_flag`` and
#: ``client_flag`` are turned OFF so no TLS extended key usage lands on a
#: document-signing certificate.
KEY_USAGE = ["DigitalSignature", "ContentCommitment"]


class BootstrapError(RuntimeError):
    """The PKI could not be provisioned. Nothing is half-applied silently."""


class Admin:
    """An admin-token client for the sys/ and pki/ paths this script writes."""

    def __init__(self, address: str, token: str, *, timeout: float = 30.0) -> None:
        self.address = address.rstrip("/")
        self._token = token
        self._timeout = timeout

    def call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return httpx.request(
                method,
                f"{self.address}/v1/{path}",
                json=body,
                headers={"X-Vault-Token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise BootstrapError(
                f"OpenBao at {self.address} could not be reached ({type(exc).__name__})."
            ) from exc

    def write(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.call("POST", path, body)
        if response.status_code >= 400:
            raise BootstrapError(f"POST {path} failed: {_errors(response)}")
        return _data(response)

    def read(self, path: str) -> dict[str, Any] | None:
        response = self.call("GET", path)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise BootstrapError(f"GET {path} failed: {_errors(response)}")
        return _data(response)


def _errors(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:200] or f"HTTP {response.status_code}"
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        return "; ".join(str(error) for error in errors)
    return f"HTTP {response.status_code}"


def _data(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _ensure_mount(admin: Admin, path: str, *, max_lease_ttl: str) -> bool:
    """Mount a PKI engine, or leave the existing one alone. True if created.

    An existing mount is never re-mounted: OpenBao would refuse anyway, and the
    important property is that a re-run cannot destroy a CA whose certificates
    are already in filed returns.
    """
    response = admin.call(
        "POST",
        f"sys/mounts/{path}",
        {"type": "pki", "config": {"max_lease_ttl": max_lease_ttl}},
    )
    if response.status_code < 400:
        return True
    message = _errors(response)
    if "already in use" in message:
        return False
    raise BootstrapError(f"Could not mount pki at {path!r}: {message}")


def _has_ca(admin: Admin, mount: str) -> bool:
    """Whether the mount already holds a CA certificate.

    ``{mount}/ca/pem`` answers 204 with an empty body when there is none, which
    is the only reliable probe: ``{mount}/cert/ca`` errors with "no default
    issuer currently configured", and an error is not a safe basis for deciding
    whether to generate a root over the top of one.
    """
    response = admin.call("GET", f"{mount}/ca/pem")
    return response.status_code == 200 and bool(response.content.strip())


def _subject(args: argparse.Namespace, common_name: str) -> dict[str, Any]:
    body: dict[str, Any] = {"common_name": common_name, "key_type": "ec", "key_bits": 256}
    if args.organization:
        body["organization"] = args.organization
    if args.country:
        body["country"] = args.country
    return body


def _ensure_root(admin: Admin, args: argparse.Namespace) -> None:
    """Generate the root CA once. Never regenerate it."""
    if _has_ca(admin, args.root_mount):
        print(f"  root CA already present on {args.root_mount!r} — left untouched")
        return
    body = _subject(args, f"{args.organization or 'AequorOS'} Attestation Root CA")
    body["ttl"] = ROOT_TTL
    admin.write(f"{args.root_mount}/root/generate/internal", body)
    print(f"  generated root CA on {args.root_mount!r} ({ROOT_TTL})")


def _ensure_intermediate(admin: Admin, args: argparse.Namespace) -> None:
    """Generate the intermediate, have the root sign it, and file it back."""
    if _has_ca(admin, args.mount):
        print(f"  issuing CA already present on {args.mount!r} — left untouched")
        return
    body = _subject(args, f"{args.organization or 'AequorOS'} Attestation Issuing CA")
    csr = admin.write(f"{args.mount}/intermediate/generate/internal", body).get("csr")
    if not isinstance(csr, str) or not csr.strip():
        raise BootstrapError(f"{args.mount} returned no intermediate CSR.")
    signed = admin.write(
        f"{args.root_mount}/root/sign-intermediate",
        {"csr": csr, "format": "pem_bundle", "ttl": INTERMEDIATE_TTL},
    ).get("certificate")
    if not isinstance(signed, str) or not signed.strip():
        raise BootstrapError(f"{args.root_mount} signed no intermediate certificate.")
    admin.write(f"{args.mount}/intermediate/set-signed", {"certificate": signed})
    print(f"  generated issuing CA on {args.mount!r}, signed by {args.root_mount!r}")


def _configure_urls(admin: Admin, mount: str, public_addr: str) -> None:
    """AIA / CRL / OCSP endpoints, baked into every certificate this mount signs.

    They must be reachable by whoever validates the document, not merely by this
    script — a CDP pointing at ``127.0.0.1`` produces certificates whose
    revocation status nobody outside the host can check, which is why
    ``--public-addr`` is separate from ``--addr``.
    """
    base = f"{public_addr.rstrip('/')}/v1/{mount}"
    admin.write(
        f"{mount}/config/urls",
        {
            "issuing_certificates": [f"{base}/ca"],
            "crl_distribution_points": [f"{base}/crl"],
            "ocsp_servers": [f"{base}/ocsp"],
        },
    )
    print(f"  configured AIA/CRL/OCSP URLs on {mount!r} → {base}")


def _ensure_role(admin: Admin, args: argparse.Namespace) -> None:
    # O and C are ROLE settings in OpenBao's PKI, not request parameters: the
    # `sign` endpoint composes the subject from the role plus the common name and
    # serial number it is given, and ignores those fields in the CSR. Setting
    # them here is what puts the institution on every officer's certificate.
    admin.write(
        f"{args.mount}/roles/{args.role}",
        {
            **({"organization": [args.organization]} if args.organization else {}),
            **({"country": [args.country]} if args.country else {}),
            # An officer's common name is a person's name, not a hostname, so
            # every DNS-shaped validation is turned off explicitly rather than
            # left to interact with allow_any_name.
            "allow_any_name": True,
            "enforce_hostnames": False,
            "cn_validations": ["disabled"],
            "require_cn": True,
            "use_csr_common_name": True,
            "use_csr_sans": False,
            # The subject identifier the platform binds signatures to. Pinning
            # the shape here means the CA itself refuses a certificate that
            # could not be tied back to a signer record.
            "allowed_serial_numbers": ["SGN-*"],
            "key_usage": KEY_USAGE,
            "ext_key_usage": [],
            "server_flag": False,
            "client_flag": False,
            "code_signing_flag": False,
            "email_protection_flag": False,
            "basic_constraints_valid_for_non_ca": True,
            # The key is generated in Transit and only its public half is ever
            # seen here, so the role accepts whichever of the two supported
            # algorithms the signer was enrolled with.
            "key_type": "any",
            "ttl": LEAF_MAX_TTL,
            "max_ttl": LEAF_MAX_TTL,
            # Stored, because an unstored certificate cannot be revoked — and a
            # revocation that cannot be published is not a revocation.
            "no_store": False,
        },
    )
    print(f"  configured signing role {args.role!r} on {args.mount!r}")


def policy_document(
    *, name: str, transit_mount: str, mount: str, role: str, key_prefix: str = "*"
) -> str:
    """The ACL the application's AppRole needs, and nothing beyond it.

    ``key_prefix`` narrows the Transit paths. The default ``*`` suits one
    OpenBao per institution; a server shared by several tenants issues one
    AppRole per tenant with ``--key-prefix OR-XXXXXXXX-*``, which is the whole
    reason ``new_transit_key_ref`` puts the organization id in the key name.
    The PKI paths are not scoped that way: the mount's role already constrains
    what may be issued, and revocation is by serial.
    """
    return (
        f"# AequorOS attestation signing ({name}). Transit holds the officers'\n"
        "# keys; the PKI mount certifies and revokes them. Nothing here can mount an\n"
        "# engine, generate a CA, or read a private key.\n"
        f'path "{transit_mount}/keys/{KEY_NAME_PREFIX}-{key_prefix}" '
        '{ capabilities = ["create", "update", "read"] }\n'
        f'path "{transit_mount}/sign/{KEY_NAME_PREFIX}-{key_prefix}" '
        '{ capabilities = ["update"] }\n'
        f'path "{mount}/sign/{role}" {{ capabilities = ["update"] }}\n'
        f'path "{mount}/revoke" {{ capabilities = ["update"] }}\n'
        f'path "{mount}/ca_chain" {{ capabilities = ["read"] }}\n'
    )


def _write_trust_anchor(admin: Admin, args: argparse.Namespace) -> None:
    """Write the root the issuing mount chains to, where the verifier reads it.

    The LAST certificate of the issuing mount's chain, resolved exactly as
    ``OpenBaoPkiIssuer.trust_anchor`` resolves it, so the file an operator
    configures and the anchor the platform believes in cannot drift apart.
    """
    response = admin.call("GET", f"{args.mount}/ca_chain")
    if response.status_code >= 400:
        raise BootstrapError(f"Could not read {args.mount}/ca_chain: {_errors(response)}")
    chain = x509.load_pem_x509_certificates(response.text.encode("ascii"))
    if not chain:
        raise BootstrapError(f"{args.mount} returned an empty CA chain.")
    root = self_signed_anchor(chain, source=f"mount {args.mount!r}")
    destination = Path(args.trust_roots)
    if destination.is_dir() or destination.suffix != ".pem":
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / "aequoros-attestation-root.pem"
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(certificates_to_pem([root]), encoding="ascii")
    print(f"  wrote the trust anchor to {destination}")
    print(f"      subject: {root.subject.rfc4514_string()}")
    print(f"      set ATTESTATION_TRUST_ROOTS={destination}")


def bootstrap(admin: Admin, args: argparse.Namespace) -> None:
    print(f"OpenBao PKI bootstrap against {admin.address}")
    _ensure_mount(admin, args.root_mount, max_lease_ttl=ROOT_TTL)
    _ensure_mount(admin, args.mount, max_lease_ttl=INTERMEDIATE_TTL)
    _ensure_root(admin, args)
    _configure_urls(admin, args.root_mount, args.public_addr or admin.address)
    _ensure_intermediate(admin, args)
    _configure_urls(admin, args.mount, args.public_addr or admin.address)
    _ensure_role(admin, args)
    if not args.no_policy:
        admin.write(
            f"sys/policies/acl/{args.policy}",
            {
                "policy": policy_document(
                    name=args.policy,
                    transit_mount=args.transit_mount,
                    mount=args.mount,
                    role=args.role,
                    key_prefix=args.key_prefix,
                )
            },
        )
        print(f"  wrote ACL policy {args.policy!r} for the application's AppRole")
    if args.trust_roots:
        _write_trust_anchor(admin, args)
    print("\nSet on the application:")
    print(f"  OPENBAO_PKI_MOUNT={args.mount}")
    print(f"  OPENBAO_PKI_ROLE={args.role}")
    if not args.trust_roots:
        print(
            "  ATTESTATION_TRUST_ROOTS=<path>   # re-run with --trust-roots <path> to\n"
            "                                   # write it; without it, verification\n"
            "                                   # anchors on the embedded chain only"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Provision the OpenBao PKI that certifies officers' signing keys."
    )
    parser.add_argument("--addr", default=os.getenv("BAO_ADDR", "http://127.0.0.1:8200"))
    parser.add_argument(
        "--token",
        default=os.getenv("BAO_TOKEN"),
        help="Admin token. Prefer the BAO_TOKEN environment variable — a token on the "
        "command line lands in the shell history.",
    )
    parser.add_argument("--root-mount", default=DEFAULT_ROOT_MOUNT)
    parser.add_argument("--mount", default=DEFAULT_ISSUING_MOUNT, help="The ISSUING mount.")
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--transit-mount", default=os.getenv("OPENBAO_TRANSIT_MOUNT", "transit"))
    parser.add_argument("--organization", default=None, help="Subject O on the CAs.")
    parser.add_argument("--country", default=None, help="Subject C on the CAs (ISO 3166-1).")
    parser.add_argument(
        "--public-addr",
        default=None,
        help="Base URL that VALIDATORS can reach, baked into CRL/OCSP/AIA URLs. "
        "Defaults to --addr, which is wrong whenever the two differ.",
    )
    parser.add_argument(
        "--trust-roots",
        default=None,
        help="Path (file or directory) to write the root CA to, for ATTESTATION_TRUST_ROOTS.",
    )
    parser.add_argument(
        "--key-prefix",
        default="*",
        help="Transit key glob the ACL policy grants, after the 'aequoros-' stem. Use "
        "'OR-XXXXXXXX-*' to scope one AppRole to one tenant on a shared server.",
    )
    parser.add_argument("--no-policy", action="store_true", help="Skip writing the ACL policy.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.token:
        print(
            "An admin token is required: set BAO_TOKEN, or pass --token. Mounting an "
            "engine and generating a CA are administrator acts that the application's "
            "AppRole deliberately cannot perform.",
            file=sys.stderr,
        )
        return 2
    try:
        bootstrap(Admin(args.addr, args.token), args)
    except (BootstrapError, SignerBackendError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
