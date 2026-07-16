"""Email fallback channel — the guided BG/FMD/2026/07 downtime workflow.

Builds a send-ready bundle (recipient guidance, subject line, attachment
list, Act 930 penalty reminder, operator instructions) and records it as the
submission event detail. No SMTP is performed in the MVP — the operator sends
the exported artifacts from their institutional mailbox.

CRITICAL semantics from docs/research/bog_orass_submission_channels.md:

- BoG Notice BG/FMD/2026/07 (CONFIRMED): email is accepted ONLY during ORASS
  downtime, and the report must be re-uploaded to ORASS once functionality is
  restored "for the submission to be deemed complete". An email submission
  therefore never completes the obligation: the package moves to
  ``submitted`` with ``{"pending_orass_reupload": true}`` and stays
  not-yet-complete for calendar/RAG purposes until the ORASS re-upload.
- The BoG downtime-return email address is UNKNOWN in the public record.
  ``bsdletters@bog.gov.gh`` is CONFIRMED only for directive-consultation
  correspondence — it must not be assumed to accept downtime returns. The
  operator uses their supervisor-provided return-desk address (optionally
  stored as ``fallback_recipient`` in the channel config).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from app.core.ids import new_uuid7
from app.models import (
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatorySubmissionEvent,
)
from app.services.regulatory_reporting.channels.base import SubmissionPollStatus
from app.services.regulatory_reporting.channels.errors import ChannelPreconditionError

EMAIL_PREFIX = "EMAIL-"

# CONFIRMED for directive-consultation correspondence only (research §3):
# "All comments shall be sent … via email at bsdletters@bog.gov.gh".
CONFIRMED_CONSULTATION_ADDRESS = "bsdletters@bog.gov.gh"
CONSULTATION_ADDRESS_NOTE = (
    "CONFIRMED for directive-consultation correspondence only — do not assume "
    "it accepts downtime returns."
)
DOWNTIME_ADDRESS_NOTE = (
    "The BoG downtime-return address is UNKNOWN in the public record "
    "(Notice BG/FMD/2026/07 does not state one). Use the return-desk address "
    "provided by your BoG supervision contact."
)
# Act 930 s.93(3) + Fines (Penalty Units) Act 572 (GH¢12/unit) — research §5.1.
ACT_930_PENALTY_REMINDER = (
    "Act 930 s.93(3): non-submission, incomplete, delayed or inaccurate "
    "submission attracts up to 500 penalty units (GH¢6,000 at GH¢12 "
    "per unit) on the institution AND the responsible key management "
    "personnel, plus 50 penalty units (GH¢600) per day the default "
    "continues."
)
REUPLOAD_RULE = (
    "Per BoG Notice BG/FMD/2026/07 this email submission is accepted only "
    "during ORASS downtime and the return must be re-uploaded to ORASS once "
    "system functionality is restored for the submission to be deemed "
    "complete. This package remains pending ORASS re-upload until then."
)


def _institution_code(package: RegulatoryPackage, config: dict[str, Any]) -> str:
    configured = config.get("institution_code")
    if configured:
        return str(configured)
    institution = package.snapshot.get("institution", {})
    return str(institution.get("short_name") or institution.get("name") or "INSTITUTION-CODE-UNSET")


def build_subject(package: RegulatoryPackage, config: dict[str, Any]) -> str:
    """'[Institution code] [Return code] [Reporting date] – submitted under ORASS downtime'."""
    return (
        f"[{_institution_code(package, config)}] [{package.return_code}] "
        f"[{package.reporting_date.isoformat()}] – submitted under ORASS downtime"
    )


def _attachment_entry(artifact: RegulatoryPackageArtifact) -> dict[str, Any]:
    return {
        "kind": artifact.kind,
        "filename": PurePosixPath(artifact.object_path).name,
        "object_path": artifact.object_path,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
    }


def build_email_bundle(
    package: RegulatoryPackage,
    artifacts: Sequence[RegulatoryPackageArtifact],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full send-ready bundle: guidance + subject + attachments + instructions."""
    config = dict(config or {})
    configured_recipient = config.get("fallback_recipient")
    subject = build_subject(package, config)
    attachments = [_attachment_entry(artifact) for artifact in artifacts]

    lines = [
        f"EMAIL FALLBACK INSTRUCTIONS — {package.return_code} "
        f"({package.reporting_date.isoformat()}, version {package.version})",
        "",
        f"1. Recipient: {DOWNTIME_ADDRESS_NOTE}",
    ]
    if configured_recipient:
        lines.append(f"   Institution-configured downtime recipient: {configured_recipient}")
    lines.extend(
        [
            f"   Reference address: {CONFIRMED_CONSULTATION_ADDRESS} — {CONSULTATION_ADDRESS_NOTE}",
            f"2. Subject line (exactly): {subject}",
            "3. Attach the exported return artifacts listed below "
            "(verify checksums before sending):",
        ]
    )
    if attachments:
        lines.extend(
            f"   - {entry['filename']} ({entry['kind']}, {entry['size_bytes']} bytes, "
            f"sha256 {entry['checksum_sha256']})"
            for entry in attachments
        )
    else:
        lines.append("   - No artifacts exported yet; export the package (xlsx/csv/pdf) first.")
    lines.extend(
        [
            f"4. {REUPLOAD_RULE}",
            f"5. Penalty reminder: {ACT_930_PENALTY_REMINDER}",
        ]
    )
    return {
        "subject": subject,
        "recipient_guidance": {
            "confirmed_consultation_address": CONFIRMED_CONSULTATION_ADDRESS,
            "confirmed_consultation_note": CONSULTATION_ADDRESS_NOTE,
            "downtime_return_address": configured_recipient,
            "downtime_return_note": DOWNTIME_ADDRESS_NOTE,
        },
        "attachments": attachments,
        "penalty_reminder": ACT_930_PENALTY_REMINDER,
        "pending_orass_reupload": True,
        "instructions": "\n".join(lines),
    }


class EmailFallbackChannel:
    """Guided downtime email submission behind the SubmissionChannel protocol."""

    channel_code = "email"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        prior_events: Sequence[RegulatorySubmissionEvent] = (),
    ) -> None:
        self._config = dict(config or {})
        self._prior_events = tuple(prior_events)
        self.last_detail: dict[str, Any] = {}

    def submit(
        self,
        package: RegulatoryPackage,
        artifacts: Sequence[RegulatoryPackageArtifact],
    ) -> str:
        if package.status != "approved":
            raise ChannelPreconditionError(
                "Only an approved package can be submitted via the email "
                f"fallback; this package is '{package.status}'."
            )
        if not artifacts:
            raise ChannelPreconditionError(
                "The package has no exported artifacts; export at least one "
                "file (xlsx/csv/pdf) before preparing the email fallback."
            )
        self.last_detail = build_email_bundle(package, artifacts, self._config)
        return f"EMAIL-{package.return_code}-{new_uuid7().hex[:12]}"

    def poll(self, external_ref: str) -> SubmissionPollStatus:
        status, _ = self.poll_with_detail(external_ref)
        return status

    def poll_with_detail(self, external_ref: str) -> tuple[SubmissionPollStatus, dict[str, Any]]:
        """Email submissions are never acknowledged: the obligation completes
        only after the ORASS re-upload (BG/FMD/2026/07)."""
        _ = external_ref
        return "pending", {
            "pending_orass_reupload": True,
            "message": ("An email fallback submission cannot be acknowledged; " + REUPLOAD_RULE),
        }
