"""RFC 3161 trusted time — the platform's only authoritative clock for
attestation (docs/attestation_esignature.md §3.6, gap **G4**).

Every timestamp the platform writes today is ``utc_now()`` — an application
host's *unmonitored wall clock*, with no server_default and nothing attesting
to it. A signature whose time rests on that clock cannot be shown to predate a
certificate revocation, which is the whole reason PAdES B-LTA and the detached
attestation both carry a timestamp token (§2.6, §3.4 "Revocation"). This module
is that trusted clock, and there is deliberately **no fallback**: with no TSA
configured the callers fail closed rather than sign with host time.

Two entry points, one for each artefact of §3.1:

* :func:`build_pdf_timestamper` — the pyHanko ``TimeStamper`` handed to
  ``PdfSigner`` for the PAdES signature and document timestamp (artefact A).
* :func:`timestamp_digest` — timestamps an arbitrary digest and returns
  ``(token_bytes, asserted_datetime)`` for the detached attestation
  (artefact B), which must stand alone where no PDF exists (confirmation
  register C1).

**Act 930 banking secrecy — only a HASH may ever leave the perimeter.**
An RFC 3161 request carries a *message imprint*: the digest, its algorithm, an
optional nonce, and nothing else. No return content, no customer data, no
figures, not one byte of the document is transmitted, which is exactly the
property that makes an external — possibly offshore — timestamping authority
compatible with s.93 secrecy (§5.2; the residency question itself is legal
register L8). :func:`_assert_only_hash_leaves` enforces this on the outgoing
request rather than trusting the comment: it re-checks that the imprint is our
digest and that no extension field is smuggling a payload.

**Nothing is trusted blindly.** pyHanko's ``handle_tsp_response`` checks the
PKI status and the nonce — it does *not* check that the token's imprint is the
digest we asked about, so a confused or hostile responder could return a token
over someone else's data. :func:`_parse_and_verify_token` re-derives that
binding before any caller sees a token.

**Scope limit, stated honestly.** This module does not validate the TSA
certificate chain: doing that requires the configured trust roots and belongs
to the verifier (§3.5 checks 1 and 3, ``pyhanko_certvalidator`` with a pinned
``ValidationContext``). What is guaranteed here is that the token is
well-formed, granted, nonce-matched, over *our* digest, and that its asserted
``genTime`` is what we recorded.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from asn1crypto import cms, tsp
from loguru import logger
from pyhanko.sign.timestamps import HTTPTimeStamper, TimeStamper
from pyhanko.sign.timestamps.common_utils import (
    TimestampRequestError,
    handle_tsp_response,
)

from app.core.config import TsaSettings, get_settings

#: Imprint algorithms we accept. Per RFC 8933 the imprint algorithm must be the
#: one that produced the digest, so this is also the set of digests a caller may
#: submit. SHA-1 is absent on purpose and must never be added.
SUPPORTED_HASH_ALGORITHMS: Final[dict[str, int]] = {
    "sha256": 32,
    "sha384": 48,
    "sha512": 64,
}


class TsaError(RuntimeError):
    """Base class for trusted-time failures. Callers must fail closed."""


class TsaNotConfigured(TsaError):
    """No ``TSA_URL`` is set, so no trusted time exists.

    Distinct from :class:`TsaUnavailable`: this is a deployment that never had a
    trusted clock, not one whose clock is momentarily unreachable. Signing must
    refuse in both cases (G4) — the distinction is for the operator's message.
    """


class TsaUnavailable(TsaError):
    """The authority could not be reached, timed out, or refused the request."""


class TsaResponseInvalid(TsaError):
    """A response arrived but cannot be trusted.

    Raised for a malformed token, a content type that is not a TST, a nonce
    mismatch, a genTime we cannot read, or — the case pyHanko does not check —
    an imprint that is not the digest we submitted.
    """


@dataclass(frozen=True)
class TimestampToken:
    """A verified RFC 3161 token plus the facts we extracted from it.

    ``token_der`` is stored verbatim in ``attestation_signatures.tsa_token``:
    the evidence must survive independently of our parsing, so the DER is the
    record and the parsed fields are conveniences.
    """

    token_der: bytes
    #: The authority's asserted genTime, normalised to UTC. Authoritative over
    #: any server clock (``declared_at`` is record-keeping only).
    asserted_at: datetime
    hash_algorithm: str
    digest: bytes
    tsa_url: str
    #: The token's serial number — what an OCSP/CRL query reveals, and the only
    #: identifier of ours the authority retains.
    serial_number: int | None


def _settings(settings: TsaSettings | None) -> TsaSettings:
    return settings if settings is not None else get_settings().tsa


def _require_configured(settings: TsaSettings) -> str:
    if not settings.enabled or settings.tsa_url is None:
        raise TsaNotConfigured(
            "No RFC 3161 authority is configured (TSA_URL is unset), so no trusted "
            "time is available. Attestation must fail closed rather than sign with "
            "the application host's wall clock (gap G4)."
        )
    return settings.tsa_url


def _require_supported_algorithm(hash_algorithm: str) -> int:
    try:
        return SUPPORTED_HASH_ALGORITHMS[hash_algorithm]
    except KeyError:
        raise TsaNotConfigured(
            f"TSA_HASH_ALGORITHM={hash_algorithm!r} is not supported; "
            f"choose one of {sorted(SUPPORTED_HASH_ALGORITHMS)}."
        ) from None


def build_pdf_timestamper(settings: TsaSettings | None = None) -> HTTPTimeStamper:
    """The ``TimeStamper`` for the PAdES path (§3.2).

    Handed to ``pyhanko.sign.signers.PdfSigner`` as its ``timestamper`` so the
    signature timestamp and the B-LTA document timestamp come from the same
    authority as the detached attestation — one clock, one audit story.
    """
    resolved = _settings(settings)
    url = _require_configured(resolved)
    _require_supported_algorithm(resolved.tsa_hash_algorithm)
    if not url.lower().startswith("https:"):
        # Not fatal: an in-country TSA may sit on a private link. But the token
        # is evidence about time, and plaintext transport invites a downgrade,
        # so the operator gets told every time a process starts one.
        logger.warning(
            "TSA_URL is not HTTPS ({url}); RFC 3161 requests will traverse plaintext "
            "transport. Only a hash is sent, but prefer HTTPS for trusted time.",
            url=url,
        )
    return HTTPTimeStamper(
        url,
        https=False,  # enforced by the warning above, not by construction
        # pyHanko's constructor is unannotated (its int default makes the checker
        # infer int); requests accepts a float timeout, which is what we want so
        # sub-second budgets stay expressible.
        timeout=resolved.tsa_timeout_seconds,  # type: ignore[arg-type]
        auth=resolved.basic_auth,
    )


def _assert_only_hash_leaves(
    request: tsp.TimeStampReq, *, digest: bytes, hash_algorithm: str
) -> None:
    """Act 930 guard: prove the outgoing request carries the hash and nothing else.

    Executable form of the secrecy claim in §5.2. An RFC 3161 request has room
    for exactly one caller-supplied payload — the message imprint — plus an
    optional extensions field. This asserts the imprint is our digest under our
    algorithm and that no extension is present, so a future change that started
    attaching request extensions cannot silently begin exporting bank data.
    """
    # asn1crypto's field access is dynamically typed (an absent OPTIONAL field is
    # a Void), so these hops are annotated Any deliberately rather than fought.
    imprint: Any = request["message_imprint"]
    sent_algorithm = imprint["hash_algorithm"]["algorithm"].native
    sent_digest = imprint["hashed_message"].native
    if sent_algorithm != hash_algorithm or not hmac.compare_digest(bytes(sent_digest), digest):
        raise TsaResponseInvalid(
            "Refusing to send a timestamp request whose message imprint is not the "
            "digest supplied by the caller."
        )
    if request["extensions"].native is not None:
        raise TsaResponseInvalid(
            "Refusing to send a timestamp request carrying extensions: only a hash "
            "may leave the perimeter (Act 930 banking secrecy, §5.2)."
        )


def _parse_and_verify_token(
    token: cms.ContentInfo, *, digest: bytes, hash_algorithm: str
) -> tuple[datetime, int | None]:
    """Verify the token is a TST over *our* digest and return (genTime, serial).

    pyHanko has already checked PKI status and the nonce. The imprint check
    here is the one it does not do: without it, a responder that timestamped a
    different document would produce a token we would happily file as evidence
    about this one.
    """
    try:
        if token["content_type"].native != "signed_data":
            raise TsaResponseInvalid(
                f"Timestamp token is not CMS SignedData "
                f"(content type {token['content_type'].native!r})."
            )
        content: Any = token["content"]
        encap: Any = content["encap_content_info"]
        if encap["content_type"].native != "tst_info":
            raise TsaResponseInvalid(
                f"Timestamp token encapsulates {encap['content_type'].native!r}, "
                f"not tst_info."
            )
        tst_info: Any = encap["content"].parsed
        imprint: Any = tst_info["message_imprint"]
        token_algorithm = imprint["hash_algorithm"]["algorithm"].native
        token_digest = bytes(imprint["hashed_message"].native)
        gen_time = tst_info["gen_time"].native
        serial_raw = tst_info["serial_number"].native
    except TsaResponseInvalid:
        raise
    except Exception as exc:  # asn1crypto raises a wide family on malformed DER
        raise TsaResponseInvalid(f"Timestamp token could not be parsed: {exc}") from exc

    if token_algorithm != hash_algorithm:
        raise TsaResponseInvalid(
            f"Timestamp token covers a {token_algorithm!r} imprint but we submitted "
            f"{hash_algorithm!r}."
        )
    if not hmac.compare_digest(token_digest, digest):
        raise TsaResponseInvalid(
            "Timestamp token does not cover the digest we submitted — it is evidence "
            "about some other data and must not be filed against this attestation."
        )
    if not isinstance(gen_time, datetime):
        raise TsaResponseInvalid("Timestamp token carries no readable genTime.")
    if gen_time.tzinfo is None:
        # RFC 3161 genTime is UTC ("Z"); a naive value means the responder or the
        # decoder dropped the zone. Treat it as UTC rather than as host-local,
        # which would silently reintroduce the host clock this module replaces.
        gen_time = gen_time.replace(tzinfo=UTC)
    serial = int(serial_raw) if isinstance(serial_raw, int) else None
    return gen_time.astimezone(UTC), serial


async def async_request_timestamp(
    digest: bytes,
    *,
    settings: TsaSettings | None = None,
    timestamper: TimeStamper | None = None,
) -> TimestampToken:
    """Timestamp ``digest`` and return the verified token.

    ``timestamper`` is an injection seam: production leaves it ``None`` and gets
    :func:`build_pdf_timestamper`'s HTTP client, while tests pass a local
    responder so the suite never touches the network.

    Only ``digest`` is transmitted (see the module docstring and
    :func:`_assert_only_hash_leaves`).
    """
    resolved = _settings(settings)
    url = _require_configured(resolved)
    hash_algorithm = resolved.tsa_hash_algorithm
    expected_size = _require_supported_algorithm(hash_algorithm)
    if not isinstance(digest, (bytes, bytearray)):
        raise ValueError("digest must be raw bytes, not hex text or a str.")
    digest = bytes(digest)
    if len(digest) != expected_size:
        raise ValueError(
            f"digest is {len(digest)} bytes; {hash_algorithm} requires {expected_size}. "
            f"Pass the raw digest, not its hex encoding."
        )

    client = timestamper if timestamper is not None else build_pdf_timestamper(resolved)
    # Drive the request in three explicit steps (all pyHanko public API) rather
    # than via async_timestamp, so the Act 930 guard runs on the exact bytes
    # that go on the wire.
    nonce, request = client.request_cms(digest, hash_algorithm)
    _assert_only_hash_leaves(request, digest=digest, hash_algorithm=hash_algorithm)
    try:
        response = await client.async_request_tsa_response(request)
        token = handle_tsp_response(response, nonce)
    except TimestampRequestError as exc:
        # TimestampRequestError subclasses IOError and pyHanko raises it both for
        # transport failure (wrapping the original OSError as __cause__) and for a
        # refused/malformed response. Classify on the cause so an operator can
        # tell "the TSA is down" from "the TSA answered something wrong".
        if isinstance(exc.__cause__, OSError):
            raise TsaUnavailable(f"Timestamp authority unreachable at {url}: {exc}") from exc
        raise TsaResponseInvalid(f"Timestamp authority at {url} returned {exc}") from exc
    except OSError as exc:
        raise TsaUnavailable(f"Timestamp authority unreachable at {url}: {exc}") from exc
    except Exception as exc:
        # handle_tsp_response indexes into the response assuming a well-formed
        # token; a responder that grants the request and returns something else
        # surfaces as TypeError/ValueError from asn1crypto. That is still an
        # untrustworthy response, not a crash for the caller to handle.
        raise TsaResponseInvalid(
            f"Timestamp authority at {url} returned an unreadable response: {exc}"
        ) from exc

    asserted_at, serial = _parse_and_verify_token(
        token, digest=digest, hash_algorithm=hash_algorithm
    )
    return TimestampToken(
        token_der=token.dump(),
        asserted_at=asserted_at,
        hash_algorithm=hash_algorithm,
        digest=digest,
        tsa_url=url,
        serial_number=serial,
    )


def request_timestamp(
    digest: bytes,
    *,
    settings: TsaSettings | None = None,
    timestamper: TimeStamper | None = None,
) -> TimestampToken:
    """Synchronous :func:`async_request_timestamp` — the signing path is sync."""
    return _run(
        async_request_timestamp(digest, settings=settings, timestamper=timestamper)
    )


def timestamp_digest(
    digest: bytes,
    *,
    settings: TsaSettings | None = None,
    timestamper: TimeStamper | None = None,
) -> tuple[bytes, datetime]:
    """Trusted time for the detached attestation: ``(token_bytes, asserted_at)``.

    The two values map straight onto ``attestation_signatures.tsa_token`` and
    ``.tsa_time``. ``asserted_at`` is the authority's genTime in UTC and is
    authoritative over ``declared_at``.

    Only the hash is sent to the authority — never document content, never
    return figures, never customer data (Act 930 s.93; §5.2).
    """
    token = request_timestamp(digest, settings=settings, timestamper=timestamper)
    return token.token_der, token.asserted_at


class TsaClient:
    """Object form of the trusted clock, injected into the signing path.

    Satisfies ``app.services.attestation.signing.TimestamperPort``
    (``timestamp_digest(digest) -> (token, time)``) and exposes ``url`` so the
    orchestrator can record which authority asserted the time on every
    signature row.
    """

    def __init__(
        self,
        settings: TsaSettings | None = None,
        *,
        timestamper: TimeStamper | None = None,
    ) -> None:
        self._settings = _settings(settings)
        # Constructing the client is where "no trusted time" surfaces, so a
        # misconfigured deployment fails at wiring time, not mid-ceremony.
        self.url = _require_configured(self._settings)
        _require_supported_algorithm(self._settings.tsa_hash_algorithm)
        self._timestamper = timestamper

    @property
    def hash_algorithm(self) -> str:
        return self._settings.tsa_hash_algorithm

    def pdf_timestamper(self) -> HTTPTimeStamper:
        """The ``TimeStamper`` for pyHanko's PAdES path (§3.2)."""
        return build_pdf_timestamper(self._settings)

    def request_timestamp(self, digest: bytes) -> TimestampToken:
        return request_timestamp(
            digest, settings=self._settings, timestamper=self._timestamper
        )

    def timestamp_digest(self, digest: bytes) -> tuple[bytes, datetime]:
        """``(token_bytes, asserted_at)`` — only the hash is transmitted."""
        token = self.request_timestamp(digest)
        return token.token_der, token.asserted_at


def get_timestamper(settings: TsaSettings | None = None) -> TsaClient | None:
    """The configured trusted clock, or ``None`` when none is configured.

    ``None`` (rather than a raise) so the platform boots and every non-signing
    surface keeps working with no TSA; the signing path is what must refuse,
    and it does so by finding no timestamper to inject.
    """
    resolved = _settings(settings)
    if not resolved.enabled:
        return None
    return TsaClient(resolved)


def _run(coro: Coroutine[Any, Any, TimestampToken]) -> TimestampToken:
    """Run a coroutine from sync code, refusing to deadlock inside a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()  # never-awaited coroutines warn; close before raising
    raise TsaError(
        "request_timestamp() was called from a running event loop; await "
        "async_request_timestamp() instead."
    )
