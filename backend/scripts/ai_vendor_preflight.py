"""Preflight: are the configured AI vendors and model ids actually CALLABLE?

Closes H-021. D-053 made the platform tiered across three vendors, but the OpenAI
and Gemini adapters were written from published contracts and exercised only
against stub transports, so their model ids were never confirmed against a live
catalogue. A wrong id is not a boot error: the vendor answers 404, the tier reads
``model_unavailable``, steps over that vendor and the deployment looks healthy
while its second and third providers are dead weight. This command is the cheap
proof, run against the target deployment's own environment before an approved
configuration is committed:

    cd backend
    uv run python scripts/ai_vendor_preflight.py
    uv run python scripts/ai_vendor_preflight.py --json > ai-preflight.json
    uv run python scripts/ai_vendor_preflight.py --vendor google

**It lists models and nothing else. No generate or completion endpoint is ever
called**, so it spends no tokens, sends no prompt and no tenant data anywhere,
and is free to run as often as you like.

Per vendor in ``AI_PROVIDER_TIER``, resolved through the same
``tiered.ADAPTER_MODULES`` the runtime uses:

1. **Credential** — present or absent, reported by ENV VAR NAME only: never the
   value, never a prefix, never a length. Absent is ``skipped: key_missing``,
   exactly what the tier records at runtime, and it is NOT an error — a
   two-vendor deployment is a legitimate deployment.
2. **Endpoint** — the vendor's documented catalogue endpoint, taken through
   ``app.core.outbound.check_url`` before the socket opens: the same
   authoritative resolving guard ``google_model`` uses. For Gemini the
   operator-supplied ``GEMINI_API_URL`` is checked too, so this command also
   proves the egress guard accepts the endpoint the draft path will POST to. A
   refusal is a reported result, not a traceback.
3. **Model** — the catalogue, and then the vendor's single-model endpoint for the
   CONFIGURED id. The second call is what separates "your credential is wrong"
   (401 -> ``not_configured``) from "your model id is wrong" (404 ->
   ``model_unavailable``), and for Gemini it is what reports whether the model
   declares ``generateContent``.

What this cannot prove, and says so rather than guessing: no vendor's catalogue
declares that a model accepts the structured-output field the adapter sends
(``google_model._SCHEMA_FIELD``, today ``responseJsonSchema``) or that it serves
a particular API surface (OpenAI's Responses API). Only a generate call would,
and this command does not make one. The catalogue facts it CAN see — the declared
generation methods, the model version — are reported instead, so the reviewer
judges from evidence rather than from a script's opinion.

Failure codes are the tier's own vocabulary, so the preflight and the runtime
name the same fault: ``key_missing``, ``endpoint_missing``, ``model_unconfigured``,
``endpoint_invalid`` (the egress guard refused), ``not_configured`` (401/403),
``model_unavailable`` (404 on the configured id), ``rate_limited``,
``upstream_error``, ``timeout``, ``connection``, ``bad_request``, plus two this
command can see that a draft cannot: ``catalogue_unavailable`` (404 on the list
endpoint — wrong host or API version) and ``generate_method_unsupported``.

Exit status: ``0`` when every vendor holding a credential passed and the rest
were skipped for a missing key; ``1`` when a vendor that HAS a credential failed
its check.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import get_settings, is_undeployed_environment
from app.core.outbound import OutboundTargetBlocked, check_url, redirect_guard
from app.services.ai import approvals, tiered
from app.services.ai.vendors import VendorUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence

    import httpx
    from pydantic import SecretStr

    from app.core.config import Settings

#: A catalogue GET that has not answered in this long has failed. Deliberately
#: NOT ``AI_REQUEST_TIMEOUT_SECONDS`` (600 s, sized for a 16k-token draft): a
#: listing call that takes minutes is a broken endpoint, and nobody running a
#: preflight should wait ten of them to be told so.
_DEFAULT_TIMEOUT_SECONDS = 20.0

#: Catalogue paging. One page covers every vendor's published catalogue today;
#: the cap exists so a paging contract change cannot turn a preflight into a
#: long walk.
_PAGE_SIZE = 1000
_MAX_PAGES = 5

#: How many look-alike catalogue ids to name when the configured one is absent.
_NEAR_MATCH_LIMIT = 5

#: The credential class and field each adapter owns. Reusing the adapters' own
#: classes means the env var NAME, the ``SecretStr`` wrapper and the
#: empty-means-unconfigured rule all come from the code the worker runs — this
#: script re-implements none of it.
_CREDENTIALS: dict[str, tuple[str, str]] = {
    "anthropic": ("AiCredentialSettings", "anthropic_api_key"),
    "openai": ("OpenAiCredentialSettings", "openai_api_key"),
    "google": ("GoogleCredentialSettings", "gemini_api_key"),
}

#: The field name the egress guard reports a refusal under, per vendor.
_ENDPOINT_FIELDS: dict[str, str] = {
    "anthropic": "anthropic_models_url",
    "openai": "openai_models_url",
    "google": "gemini_models_url",
}

#: The operator-supplied setting the Gemini adapter validates. Named so a
#: refusal reads as "GEMINI_API_URL was refused", the same label the adapter uses.
_GEMINI_URL_FIELD = "GEMINI_API_URL"

#: OpenAI's published catalogue endpoint, on the same host the adapter POSTs a
#: draft to (``openai_model._API_BASE``); a test pins the two together so a base
#: URL change cannot leave the preflight checking a different host.
_OPENAI_MODELS_URL = "https://api.openai.com/v1/models"

#: Path grammar shared by the Gemini generate and list endpoints. Same constant
#: as ``google_model._MODELS_SEGMENT``, and a test pins that the list URL is the
#: configured generate URL with its ``<model>:generateContent`` tail removed.
_MODELS_SEGMENT = "models"
_GENERATE_METHOD = "generateContent"


@dataclass
class VendorCheck:
    """One vendor's result. Nothing here can carry a credential value."""

    vendor: str
    tier_position: int
    #: The NAME of the env var that holds this vendor's key. Never its value.
    credential_env: str
    credential_configured: bool
    configured_model: str | None = None
    #: The catalogue URL, stripped of query and fragment before it is printed or
    #: requested — so a key an operator wrote into ``GEMINI_API_URL`` cannot
    #: reach this output, exactly as ``google_model._endpoint`` drops it.
    endpoint: str | None = None
    #: Did the host answer at all? A 401 counts: the endpoint was reached.
    endpoint_reachable: bool | None = None
    model_present: bool | None = None
    #: ``ok`` | ``skipped`` | ``failed``.
    status: str = "failed"
    failure_code: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class PreflightReport:
    app_env: str
    model_backend: str
    provider_tier: list[str]
    vendors: list[VendorCheck]
    warnings: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[VendorCheck]:
        return [check for check in self.vendors if check.status == "failed"]

    @property
    def exit_code(self) -> int:
        """Non-zero only for a vendor that HAS a key and failed anyway."""
        return 1 if self.failures else 0

    def as_json(self) -> dict[str, Any]:
        return {
            "app_env": self.app_env,
            "model_backend": self.model_backend,
            "provider_tier": self.provider_tier,
            "vendors": [vars(check) for check in self.vendors],
            "summary": {
                "checked": len(self.vendors),
                "passed": len([c for c in self.vendors if c.status == "ok"]),
                "skipped": len([c for c in self.vendors if c.status == "skipped"]),
                "failed": len(self.failures),
            },
            "warnings": self.warnings,
            "exit_code": self.exit_code,
        }


# --- shared helpers ---------------------------------------------------------


def _strip_query(url: str) -> str:
    """Scheme, host and path only.

    Any query string is dropped before the URL is printed OR requested, which is
    what keeps a credential out of this command's output on the one endpoint an
    operator supplies by hand: a ``?key=...`` in ``GEMINI_API_URL`` never reaches
    stdout, a log, or the wire. ``google_model._endpoint`` drops it too.
    """
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _adapter(vendor: str) -> Any:
    """The vendor's adapter module, resolved by name exactly as the tier does."""
    return importlib.import_module(tiered.ADAPTER_MODULES[vendor])


def _credential(module: Any, vendor: str) -> tuple[str, SecretStr | None]:
    """``(env var NAME, the key)`` from the adapter's own credential class."""
    class_name, field_name = _CREDENTIALS[vendor]
    settings_class = getattr(module, class_name)
    alias = settings_class.model_fields[field_name].alias
    return str(alias or field_name.upper()), getattr(settings_class(), field_name)


def _http_failure(status: int) -> str:
    """HTTP status -> the tier's failure vocabulary. ``not_found`` is contextual,
    so the caller names it ``catalogue_unavailable`` or ``model_unavailable``."""
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return "not_configured"
    if status == HTTPStatus.NOT_FOUND:
        return "not_found"
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return "rate_limited"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "upstream_error"
    return "bad_request"


def _fail(check: VendorCheck, code: str, note: str) -> None:
    check.status = "failed"
    check.failure_code = code
    check.notes.append(note)


def _pass(check: VendorCheck, note: str) -> None:
    check.status = "ok"
    check.failure_code = None
    check.notes.append(note)


def _transport_note(check: VendorCheck, code: str) -> None:
    """A vendor host that did not answer. No exception text is quoted: an httpx
    message carries the URL, and a URL is the one place a credential can hide."""
    check.endpoint_reachable = False
    if code == "timeout":
        _fail(check, code, f"no answer within the timeout from {check.endpoint}")
        return
    _fail(check, code, f"could not connect to {check.endpoint} (DNS, TLS or a reset socket)")


def _catalogue_failure(check: VendorCheck, code: str) -> None:
    """The listing call answered, but not with a catalogue."""
    check.endpoint_reachable = True
    if code == "not_configured":
        _fail(
            check,
            code,
            f"the vendor rejected {check.credential_env} (401/403): wrong, revoked,"
            " or issued for another account",
        )
        return
    if code == "not_found":
        _fail(
            check,
            "catalogue_unavailable",
            f"{check.endpoint} answered 404: wrong host or API version for this vendor",
        )
        return
    if code == "rate_limited":
        _fail(check, code, "the vendor answered 429, so the check is inconclusive — retry")
        return
    if code == "upstream_error":
        _fail(check, code, "the vendor's catalogue is erroring (5xx), so the check is inconclusive")
        return
    _fail(check, code, f"the catalogue call was rejected ({code})")


def _near_matches(model: str, catalogue: Sequence[str]) -> list[str]:
    """Catalogue ids that LOOK like the configured one — a typo, or a dated
    snapshot of it. Named as evidence; never substituted for what was asked for."""
    stem = model.rsplit("-", 1)[0] if "-" in model else model
    matches = [
        name for name in catalogue if name != model and (name.startswith(stem) or stem in name)
    ]
    return sorted(matches)[:_NEAR_MATCH_LIMIT]


def _model_outcome(check: VendorCheck, catalogue: Sequence[str], code: str | None) -> None:
    """The single-model call's verdict. This is the H-021 question."""
    model = check.configured_model or ""
    if code == "not_found":
        check.model_present = False
        hints = _near_matches(model, catalogue)
        suffix = f"; the catalogue offers {', '.join(hints)}" if hints else ""
        _fail(
            check,
            "model_unavailable",
            f"the vendor does not know the configured model {model!r}"
            f" — every draft would 404 and the tier would step over this vendor{suffix}",
        )
        return
    if code is not None:
        check.model_present = None
        _fail(check, code, f"the check for model {model!r} was rejected ({code})")
        return
    check.model_present = True
    listed = model in catalogue
    _pass(check, f"{model!r} is callable with {check.credential_env}")
    if not listed:
        check.notes.append(
            f"{model!r} answers on its own endpoint but is absent from the listed"
            " catalogue (a legacy or unlisted model)"
        )


# --- per-vendor probes ------------------------------------------------------


def _probe_anthropic(check: VendorCheck, secret: SecretStr, *, timeout: float, stub: Any) -> None:
    """``GET /v1/models`` and ``GET /v1/models/{id}`` through the locked SDK.

    The SDK rather than a hand-written request, because it is the vendor's own
    verified client and it is exactly what the draft path uses — including the
    explicit ``api_key=`` that ``client.py`` documents. It ships its own fork of
    httpx (``httpx2``), so the test seam here is an ``httpx2.Client``, not the
    ``httpx.BaseTransport`` the other two adapters take.
    """
    import anthropic  # noqa: PLC0415 - the vendor's client, imported lazily as in client.py

    extra: dict[str, Any] = {"http_client": stub} if stub is not None else {}
    client = anthropic.Anthropic(
        api_key=secret.get_secret_value(), timeout=timeout, max_retries=0, **extra
    )
    # The SDK honours ANTHROPIC_BASE_URL, so this reads the host it will really
    # call rather than a constant this script asserts.
    check.endpoint = _strip_query(f"{str(client.base_url).rstrip('/')}/{_MODELS_SEGMENT}")
    check_url(check.endpoint, field=_ENDPOINT_FIELDS["anthropic"])

    catalogue: list[str] = []
    try:
        page = client.models.list(limit=_PAGE_SIZE)
        for _ in range(_MAX_PAGES):
            catalogue.extend(str(entry.id) for entry in page.data)
            if not page.has_more or not catalogue:
                break
            page = client.models.list(limit=_PAGE_SIZE, after_id=catalogue[-1])
    except anthropic.APIError as exc:
        code = _anthropic_failure(anthropic, exc)
        if code in {"timeout", "connection"}:
            _transport_note(check, code)
        else:
            _catalogue_failure(check, code)
        return
    check.endpoint_reachable = True

    try:
        client.models.retrieve(check.configured_model or "")
    except anthropic.APIError as exc:
        _model_outcome(check, catalogue, _anthropic_failure(anthropic, exc))
        return
    _model_outcome(check, catalogue, None)


def _anthropic_failure(sdk: Any, exc: Exception) -> str:
    """An SDK exception -> the same vocabulary a status code would produce."""
    if isinstance(exc, sdk.APITimeoutError):
        return "timeout"
    if isinstance(exc, sdk.APIConnectionError):
        return "connection"
    status = getattr(exc, "status_code", None)
    return _http_failure(status) if isinstance(status, int) else "upstream_error"


def _probe_openai(check: VendorCheck, secret: SecretStr, *, timeout: float, stub: Any) -> None:
    """``GET /v1/models`` and ``GET /v1/models/{id}``, Bearer credential.

    The catalogue does not say which API surface a model serves, so a pass here
    means "this account may use this model id", not "the Responses API request in
    ``openai_model`` is accepted" — only a generate call proves that, and this
    command makes none.
    """
    check.endpoint = _OPENAI_MODELS_URL
    check_url(check.endpoint, field=_ENDPOINT_FIELDS["openai"])
    headers = {"Authorization": f"Bearer {secret.get_secret_value()}"}
    with _http_client(headers, field=_ENDPOINT_FIELDS["openai"], timeout=timeout, stub=stub) as c:
        response, transport_code = _get(c, check.endpoint)
        if response is None:
            _transport_note(check, transport_code or "connection")
            return
        if response.status_code != HTTPStatus.OK:
            _catalogue_failure(check, _http_failure(response.status_code))
            return
        check.endpoint_reachable = True
        body = _body(response)
        if body is None:
            _fail(check, "parse_failed", f"{check.endpoint} did not answer with JSON")
            return
        catalogue = [
            str(entry.get("id"))
            for entry in body.get("data") or ()
            if isinstance(entry, dict) and entry.get("id")
        ]
        model = check.configured_model or ""
        response, transport_code = _get(c, f"{check.endpoint}/{model}")
        if response is None:
            _transport_note(check, transport_code or "connection")
            return
        code = (
            None if response.status_code == HTTPStatus.OK else _http_failure(response.status_code)
        )
        _model_outcome(check, catalogue, code)
        check.notes.append(
            "the catalogue does not declare API-surface support, so the Responses"
            " API shape itself stays unverified (H-021)"
        )


def _probe_google(
    check: VendorCheck, settings: Settings, secret: SecretStr, *, timeout: float, stub: Any
) -> None:
    """``GET {base}/models`` and ``GET {base}/models/{id}``, ``x-goog-api-key``.

    Both the operator's ``GEMINI_API_URL`` and the derived list URL go through
    ``check_url``, so a run of this command is also the proof that the egress
    guard accepts the endpoint the draft path will POST to.
    """
    module = _adapter("google")
    raw = module.GoogleEndpointSettings().gemini_api_url or ""
    generate_url = _strip_query(raw)
    # The adapter's own check, on the adapter's own field name, so a refusal here
    # reads exactly as the one a draft would hit.
    check_url(generate_url, field=_GEMINI_URL_FIELD)
    check.endpoint = _models_url(generate_url)
    check_url(check.endpoint, field=_ENDPOINT_FIELDS["google"])

    headers = {"x-goog-api-key": secret.get_secret_value()}
    with _http_client(headers, field=_ENDPOINT_FIELDS["google"], timeout=timeout, stub=stub) as c:
        catalogue: list[str] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"pageSize": _PAGE_SIZE}
            if token:
                params["pageToken"] = token
            response, transport_code = _get(c, check.endpoint, params=params)
            if response is None:
                _transport_note(check, transport_code or "connection")
                return
            if response.status_code != HTTPStatus.OK:
                _catalogue_failure(check, _http_failure(response.status_code))
                return
            check.endpoint_reachable = True
            body = _body(response)
            if body is None:
                _fail(check, "parse_failed", f"{check.endpoint} did not answer with JSON")
                return
            catalogue.extend(
                _google_model_id(entry)
                for entry in body.get(_MODELS_SEGMENT) or ()
                if isinstance(entry, dict)
            )
            token = body.get("nextPageToken")
            if not token:
                break

        model = check.configured_model or ""
        response, transport_code = _get(c, f"{check.endpoint}/{model}")
        if response is None:
            _transport_note(check, transport_code or "connection")
            return
        if response.status_code != HTTPStatus.OK:
            _model_outcome(check, catalogue, _http_failure(response.status_code))
            return
        entry = _body(response) or {}
        _model_outcome(check, catalogue, None)
        if check.status != "ok":
            return
        _google_capabilities(check, entry, settings)


def _google_capabilities(check: VendorCheck, entry: Mapping[str, Any], settings: Settings) -> None:
    """The two capability questions the Gemini adapter depends on.

    The generation method IS declared by the catalogue, so it is checked. Whether
    the model accepts ``google_model._SCHEMA_FIELD`` is NOT declared anywhere in
    the models API, so it is reported as unverified with the declared version
    beside it — the reviewer decides, and the adapter names the one line to change
    if the answer turns out to be the older OpenAPI-subset ``responseSchema``.
    """
    methods = [str(name) for name in entry.get("supportedGenerationMethods") or ()]
    if methods and _GENERATE_METHOD not in methods:
        check.notes.append(f"declared generation methods: {', '.join(methods) or 'none'}")
        _fail(
            check,
            "generate_method_unsupported",
            f"{check.configured_model!r} does not declare {_GENERATE_METHOD}",
        )
        return
    if not methods:
        check.notes.append("the catalogue declared no generation methods for this model")
    else:
        check.notes.append(f"declares {_GENERATE_METHOD}")
    schema_field = getattr(_adapter("google"), "_SCHEMA_FIELD", "responseJsonSchema")
    version = entry.get("version") or entry.get("baseModelId") or "unstated"
    check.notes.append(
        f"structured output ({schema_field}) is not declared by the catalogue and stays"
        f" unverified here — declared version {version}"
    )
    if settings.ai.google_model.strip():
        return
    check.notes.append("AI_GOOGLE_MODEL is unset, so the model came from GEMINI_API_URL")


def _models_url(generate_url: str) -> str:
    """The catalogue URL that is a sibling of the configured generate URL.

    ``…/v1beta/models/<id>:generateContent`` -> ``…/v1beta/models``. Derived from
    the operator's own endpoint rather than a constant, so a deployment pointed at
    a proxy or a different API version is checked where it actually calls.
    """
    parts = urlsplit(generate_url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if _MODELS_SEGMENT in segments:
        segments = segments[: segments.index(_MODELS_SEGMENT)]
    path = "/" + "/".join([*segments, _MODELS_SEGMENT])
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _google_model_id(entry: Mapping[str, Any]) -> str:
    """``models/gemini-2.5-pro`` -> ``gemini-2.5-pro``."""
    return str(entry.get("name") or "").split("/")[-1]


def _http_client(headers: dict[str, str], *, field: str, timeout: float, stub: Any) -> httpx.Client:
    """The adapters' client, minus the POST: redirects refused, every hop guarded."""
    import httpx  # noqa: PLC0415 - the transport is imported lazily, as in the adapters

    return httpx.Client(
        headers={**headers, "Accept": "application/json"},
        timeout=timeout,
        transport=stub or httpx.HTTPTransport(retries=0),
        follow_redirects=False,
        event_hooks={"response": [redirect_guard(field=field)]},
    )


def _get(
    client: httpx.Client, url: str, *, params: dict[str, Any] | None = None
) -> tuple[httpx.Response | None, str | None]:
    """``(response, None)`` or ``(None, failure code)``. Raises nothing but an
    egress refusal, which the caller reports."""
    import httpx  # noqa: PLC0415 - the transport is imported lazily, as in the adapters

    try:
        return client.get(url, params=params), None
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.TransportError:
        return None, "connection"


def _body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


# --- the report -------------------------------------------------------------


def check_vendor(
    vendor: str,
    position: int,
    settings: Settings,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    stub: Any = None,
) -> VendorCheck:
    """One vendor, in the order the tier resolves it: credential, endpoint, model."""
    module = _adapter(vendor)
    env_name, secret = _credential(module, vendor)
    check = VendorCheck(
        vendor=vendor,
        tier_position=position,
        credential_env=env_name,
        credential_configured=secret is not None,
    )
    # What the adapter WOULD send, credential-free — for Gemini this is where
    # AI_GOOGLE_MODEL or the id inside GEMINI_API_URL is resolved.
    describe_code: str | None = None
    try:
        check.configured_model = module.describe(settings).model
    except VendorUnavailable as unavailable:
        describe_code = unavailable.code

    if secret is None:
        check.status = "skipped"
        check.failure_code = "key_missing"
        check.notes.append(
            f"{env_name} is not set, so the tier skips this vendor by name"
            " — a two-vendor deployment is legitimate"
        )
        return check
    if describe_code is not None:
        _fail(check, describe_code, _describe_note(vendor, describe_code))
        return check

    try:
        if vendor == "anthropic":
            _probe_anthropic(check, secret, timeout=timeout, stub=stub)
        elif vendor == "openai":
            _probe_openai(check, secret, timeout=timeout, stub=stub)
        else:
            _probe_google(check, settings, secret, timeout=timeout, stub=stub)
    except OutboundTargetBlocked as blocked:
        # Includes a redirect to a destination the guard refuses. Only the
        # guard's reason is reported: its internal detail can carry a resolved
        # address, and a URL is the one place a credential can hide.
        check.endpoint_reachable = False
        _fail(
            check,
            "endpoint_invalid",
            f"the egress guard refused {blocked.field} ({blocked.reason}); nothing was sent",
        )
    return check


def _describe_note(vendor: str, code: str) -> str:
    if code == "endpoint_missing":
        return f"{_GEMINI_URL_FIELD} is not set, so no endpoint could be derived"
    if code == "model_unconfigured":
        return (
            "no model id is configured for this vendor"
            if vendor != "google"
            else f"no model id: set AI_GOOGLE_MODEL or name one in {_GEMINI_URL_FIELD}"
        )
    return f"this vendor cannot be prepared in this deployment ({code})"


def build_report(
    settings: Settings | None = None,
    *,
    vendors: Sequence[str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    stubs: Mapping[str, Any] | None = None,
) -> PreflightReport:
    """Check every vendor in the tier. ``stubs`` is the test seam and nothing else.

    Product runs pass no stub: the Anthropic probe then builds the SDK's own
    client and the other two build ``httpx.HTTPTransport``. A test passes an
    ``httpx2.Client`` for ``anthropic`` and an ``httpx.MockTransport`` for the
    other two, which is why the seam is per vendor rather than one object.
    """
    settings = settings or get_settings()
    order = list(settings.ai.provider_order)
    selected = [vendor for vendor in order if vendors is None or vendor in vendors]
    report = PreflightReport(
        app_env=settings.app.app_env,
        model_backend=settings.ai.model_backend,
        provider_tier=order,
        vendors=[
            check_vendor(
                vendor,
                order.index(vendor) + 1,
                settings,
                timeout=timeout,
                stub=(stubs or {}).get(vendor),
            )
            for vendor in selected
        ],
    )
    _add_warnings(report, settings)
    return report


def _add_warnings(report: PreflightReport, settings: Settings) -> None:
    """Deployment facts that decide whether a callable vendor is ever called."""
    if settings.ai.model_backend != "tiered":
        report.warnings.append(
            f"AI_MODEL_BACKEND={settings.ai.model_backend}: the tier is pinned, so only"
            f" {', '.join(report.provider_tier)} can be reached at runtime"
        )
    if not settings.ai.commentary_enabled:
        report.warnings.append(
            "AI_COMMENTARY_ENABLED is off: this deployment calls no model today."
            " Callability is still worth knowing before it is switched on."
        )
    if is_undeployed_environment(settings.app.app_env):
        return
    approved = {
        (entry.vendor, entry.model)
        for entry in approvals.load_approvals()
        if entry.app_env == settings.app.app_env
    }
    for check in report.vendors:
        if check.status != "ok":
            continue
        if (check.vendor, check.configured_model or "") not in approved:
            check.notes.append(
                "callable, but no approved configuration names this vendor and model in"
                f" app_env={settings.app.app_env}, so the tier will still skip it (D-053)"
            )


# --- output -----------------------------------------------------------------


def _credential_cell(check: VendorCheck) -> str:
    return f"{check.credential_env} {'set' if check.credential_configured else 'unset'}"


def _endpoint_cell(check: VendorCheck) -> str:
    if check.endpoint_reachable is None:
        return "-"
    return "reachable" if check.endpoint_reachable else "no answer"


def _catalogue_cell(check: VendorCheck) -> str:
    if check.model_present is None:
        return "-"
    return "yes" if check.model_present else "NO"


def render_table(report: PreflightReport) -> str:
    """A fixed-width table a reviewer can paste into a deployment record."""
    if not report.vendors:
        return "no vendor selected"
    header: tuple[str, ...] = (
        "vendor",
        "tier",
        "credential",
        "endpoint",
        "configured model",
        "in catalogue",
        "notes",
    )
    lines: list[tuple[str, ...]] = [header]
    for check in report.vendors:
        lines.append(
            (
                check.vendor,
                str(check.tier_position),
                _credential_cell(check),
                _endpoint_cell(check),
                check.configured_model or "-",
                _catalogue_cell(check),
                f"{check.status}"
                + (f" ({check.failure_code})" if check.failure_code else "")
                + (f": {'; '.join(check.notes)}" if check.notes else ""),
            )
        )
    widths = [max(len(line[index]) for line in lines) for index in range(len(header))]
    out: list[str] = []
    for number, line in enumerate(lines):
        out.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)).rstrip())
        if number == 0:
            out.append("  ".join("-" * width for width in widths))
    return "\n".join(out)


def render_summary(report: PreflightReport) -> str:
    """Endpoints, warnings and the verdict — everything the table cannot hold."""
    out: list[str] = ["", "Endpoints checked (listing only — no generate call was made):"]
    for check in report.vendors:
        out.append(f"  {check.vendor}  {check.endpoint or '(none derived)'}")
    for warning in report.warnings:
        out.extend(["", f"! {warning}"])
    passed = [check for check in report.vendors if check.status == "ok"]
    skipped = [check for check in report.vendors if check.status == "skipped"]
    out.append("")
    out.append(
        f"{len(passed)} vendor(s) callable, {len(skipped)} skipped for a missing key,"
        f" {len(report.failures)} failed."
    )
    if report.failures:
        out.append(
            "A vendor with a credential failed its check: "
            + ", ".join(f"{c.vendor} ({c.failure_code})" for c in report.failures)
            + ". Fix it or remove the vendor from AI_PROVIDER_TIER."
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--vendor",
        action="append",
        default=None,
        help="check only this vendor (repeatable); default every vendor in AI_PROVIDER_TIER",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"seconds to wait for a catalogue call (default {_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    settings = get_settings()
    order = settings.ai.provider_order
    unknown = [vendor for vendor in args.vendor or () if vendor not in order]
    if unknown:
        parser.error(f"{', '.join(unknown)} not in AI_PROVIDER_TIER ({', '.join(order)})")

    report = build_report(settings, vendors=args.vendor, timeout=args.timeout)
    if args.json:
        print(json.dumps(report.as_json(), indent=2))
        return report.exit_code
    print(render_table(report))
    print(render_summary(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
