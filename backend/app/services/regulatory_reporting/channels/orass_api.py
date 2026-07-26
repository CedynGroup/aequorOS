"""ORASS API channel — the production machine-to-machine client.

BoG's ORASS platform ships a vendor submission API ("Vizor API Service –
Submit"; research §2.3), and onboarding discussions for API access are in
progress. This client is built production-shaped NOW so that onboarding is a
configuration event, not a code change:

- Transport, auth, timeouts, and TLS verification are real (httpx).
- The WIRE CONTRACT below (paths, field names, response shapes) is
  AequorOS-provisional: it will be aligned to the BoG/Regnology-issued
  specification during onboarding and is deliberately concentrated in the
  ``_Wire`` block so realignment touches one place. Nothing here is presented
  to users as the official contract (see PROVISIONAL_CONTRACT_NOTE).
- Regulator responses are mapped onto the same poll statuses the rest of the
  pipeline speaks (pending/acknowledged/rejected/declined) and supervisor
  comments are carried through verbatim in the event detail.
- Failure discipline mirrors the market-data adapters: no raw vendor error
  ever reaches a bank-facing surface; connectivity failures surface as
  channel downtime, which routes the operator into the BG/FMD/2026/07 email
  fallback (the exact behavior the directive prescribes for ORASS outages).

Config (channel ``orass_api``): ``api_base_url`` (required), ``auth_mode``
(``api_key`` | ``basic``, default ``api_key``), ``institution_code``,
``timeout_seconds`` (default 30), ``verify_tls`` (default true).
Credentials (vaulted, write-only): ``api_key`` for bearer auth, or
``username``/``password`` for basic auth.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from app.models import RegulatoryPackage
from app.services.regulatory_reporting.channels.base import (
    FiledArtifact,
    SubmissionPollStatus,
)
from app.services.regulatory_reporting.channels.errors import (
    ChannelDowntimeError,
    ChannelError,
    ChannelPreconditionError,
)

PROVISIONAL_CONTRACT_NOTE = (
    "ORASS API wire contract is AequorOS-provisional pending the "
    "BoG/Regnology-issued specification; transport and lifecycle are "
    "production-real, field names will be aligned at onboarding."
)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_SUBMITTABLE_STATUSES = ("approved", "submitted")

_ARTIFACT_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "application/zip",  # multi-section CSV exports are zip bundles
    "pdf": "application/pdf",
}

# Regulator-side states we accept from the API, normalized to our poll enum.
_STATUS_MAP: dict[str, SubmissionPollStatus] = {
    "pending": "pending",
    "processing": "pending",
    "submitted_for_approval": "pending",
    "acknowledged": "acknowledged",
    "approved": "acknowledged",
    "rejected": "rejected",
    "declined": "declined",
}


class _Wire:
    """The provisional wire contract, concentrated for one-place realignment."""

    @staticmethod
    def submit_path(return_code: str) -> str:
        return f"/api/v1/returns/{return_code}/submissions"

    @staticmethod
    def poll_path(reference: str) -> str:
        return f"/api/v1/submissions/{reference}"

    @staticmethod
    def resubmission_path(reference: str) -> str:
        return f"/api/v1/submissions/{reference}/resubmission-requests"

    @staticmethod
    def submit_metadata(package: RegulatoryPackage, institution_code: str | None) -> dict[str, Any]:
        return {
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "version": package.version,
            "institution_code": institution_code,
        }


def _operator_safe(exc: httpx.HTTPError) -> str:
    """Classify transport errors without leaking wire internals to operators."""
    if isinstance(exc, httpx.TimeoutException):
        return "The ORASS API did not respond within the configured timeout."
    if isinstance(exc, httpx.ConnectError):
        return "The ORASS API endpoint could not be reached."
    return "The ORASS API request failed."


class OrassApiChannel:
    """Production ORASS submission client behind the SubmissionChannel seam.

    Prior events are accepted for interface parity with the sandbox but are
    not consulted — regulator-side state is authoritative here.
    """

    channel_code = "orass_api"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
        prior_events: Sequence[Any] = (),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = dict(config or {})
        self._credentials = dict(credentials or {})
        self._transport = transport  # test seam (httpx.MockTransport)
        self.last_detail: dict[str, Any] = {}

    # -- configuration -------------------------------------------------------

    @property
    def _base_url(self) -> str:
        base_url = str(self._config.get("api_base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ChannelPreconditionError(
                "The ORASS API channel is not configured: set api_base_url in "
                "the channel configuration (Settings -> Regulatory Reporting)."
            )
        return base_url

    def _auth_headers(self) -> dict[str, str]:
        mode = str(self._config.get("auth_mode") or "api_key")
        if mode == "basic":
            username = str(self._credentials.get("username") or "")
            password = str(self._credentials.get("password") or "")
            if not username or not password:
                raise ChannelPreconditionError(
                    "The ORASS API channel has no stored username/password "
                    "credentials; save them in the channel configuration."
                )
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        api_key = str(self._credentials.get("api_key") or "")
        if not api_key:
            raise ChannelPreconditionError(
                "The ORASS API channel has no stored API key; save it in the channel configuration."
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _client(self) -> httpx.Client:
        timeout = float(self._config.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)
        verify = bool(self._config.get("verify_tls", True))
        return httpx.Client(
            base_url=self._base_url,
            headers=self._auth_headers(),
            timeout=timeout,
            verify=verify,
            transport=self._transport,
        )

    # -- protocol ------------------------------------------------------------

    def submit(
        self,
        package: RegulatoryPackage,
        artifacts: Sequence[FiledArtifact],
    ) -> str:
        if package.status not in _SUBMITTABLE_STATUSES:
            raise ChannelPreconditionError(
                "Only an approved package (or the ORASS re-upload of a downtime "
                f"email submission) can be submitted; this package is '{package.status}'."
            )
        if not artifacts:
            raise ChannelPreconditionError(
                "The package has no exported artifacts; export at least one "
                "file (xlsx/csv/pdf) before submitting."
            )
        institution_code = self._config.get("institution_code")
        payload = _Wire.submit_metadata(package, institution_code)
        artifact_manifest = [
            {
                "kind": artifact.kind,
                "checksum_sha256": artifact.checksum_sha256,
                "size_bytes": artifact.size_bytes,
                "content_type": _ARTIFACT_CONTENT_TYPES.get(
                    artifact.kind, "application/octet-stream"
                ),
            }
            for artifact in artifacts
        ]
        body = {"metadata": payload, "artifacts": artifact_manifest}
        data = self._request("POST", _Wire.submit_path(package.return_code), json=body)
        reference = str(data.get("reference") or "").strip()
        if not reference:
            raise ChannelError(
                "The ORASS API accepted the request but returned no submission "
                "reference; contact support before retrying.",
                internal_detail=f"missing reference in response keys={sorted(data)}",
            )
        self.last_detail = {
            "provisional_contract": True,
            "note": PROVISIONAL_CONTRACT_NOTE,
            "institution_code": institution_code,
            "artifact_kinds": sorted(artifact.kind for artifact in artifacts),
            "response_status": data.get("status"),
        }
        return reference

    def poll(self, external_ref: str) -> SubmissionPollStatus:
        poll_status, _ = self.poll_with_detail(external_ref)
        return poll_status

    def poll_with_detail(self, external_ref: str) -> tuple[SubmissionPollStatus, dict[str, Any]]:
        data = self._request("GET", _Wire.poll_path(external_ref))
        raw_status = str(data.get("status") or "").strip().lower()
        normalized = _STATUS_MAP.get(raw_status)
        if normalized is None:
            raise ChannelError(
                "The ORASS API returned an unrecognized submission status; contact support.",
                internal_detail=f"unmapped status '{raw_status}' for ref {external_ref}",
            )
        detail: dict[str, Any] = {
            "provisional_contract": True,
            "note": PROVISIONAL_CONTRACT_NOTE,
            "regulator_status": raw_status,
        }
        comments = data.get("comments")
        if comments:
            detail["comments"] = str(comments)
            detail["message"] = str(comments)
        return normalized, detail

    def request_resubmission(self, external_ref: str, reason: str) -> tuple[str, dict[str, Any]]:
        """File a formal resubmission request; returns (status, detail).

        Status is ``granted`` | ``denied`` | ``requested`` (still pending at
        the regulator) per the provisional contract.
        """
        data = self._request("POST", _Wire.resubmission_path(external_ref), json={"reason": reason})
        raw = str(data.get("status") or "requested").strip().lower()
        status = raw if raw in ("granted", "denied", "requested") else "requested"
        detail: dict[str, Any] = {
            "provisional_contract": True,
            "note": PROVISIONAL_CONTRACT_NOTE,
            "regulator_status": raw,
        }
        if data.get("comments"):
            detail["comments"] = str(data["comments"])
        return status, detail

    # -- transport -----------------------------------------------------------

    def _request(self, method: str, path: str, *, json: Any | None = None) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            # Connectivity/timeout failures ARE the ORASS-downtime scenario:
            # BG/FMD/2026/07 routes the operator to the email fallback.
            raise ChannelDowntimeError(
                f"{_operator_safe(exc)} Per BoG Notice BG/FMD/2026/07, submit "
                "via the email fallback channel now and re-upload through ORASS "
                "once system functionality is restored for the submission to be "
                "deemed complete.",
                internal_detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        if response.status_code >= 500:
            raise ChannelDowntimeError(
                "The ORASS API reported a server error. Per BoG Notice "
                "BG/FMD/2026/07, submit via the email fallback channel now and "
                "re-upload through ORASS once system functionality is restored.",
                internal_detail=f"HTTP {response.status_code}",
            )
        if response.status_code in (401, 403):
            raise ChannelPreconditionError(
                "The ORASS API rejected the stored credentials; update them in "
                "the channel configuration."
            )
        if response.status_code >= 400:
            raise ChannelError(
                "The ORASS API rejected the request; verify the channel "
                "configuration and the return contents.",
                internal_detail=f"HTTP {response.status_code}: {response.text[:500]}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ChannelError(
                "The ORASS API returned an unreadable response; contact support.",
                internal_detail=f"non-JSON body: {response.text[:200]}",
            ) from exc
        if not isinstance(payload, dict):
            raise ChannelError(
                "The ORASS API returned an unexpected response shape; contact support.",
                internal_detail=f"non-object JSON: {type(payload).__name__}",
            )
        return payload
