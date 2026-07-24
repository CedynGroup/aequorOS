"""ORASS sandbox channel — a clearly-labeled deterministic simulator.

No public API endpoint URLs, field names, payload schemas, or credential
mechanics for ORASS exist anywhere in the public record
(docs/research/bog_orass_submission_channels.md, anti-hallucination note).
This channel therefore simulates only publicly-evidenced behaviors — submit,
status lifecycle, downtime — behind the :class:`SubmissionChannel` seam, and
labels every response as a simulation: every external_ref and simulated
regulator response is prefixed ``SANDBOX-`` and every detail payload carries
``{"sandbox": true, "note": ...}``. Real ORASS onboarding (BoG/Regnology-
issued specs + credentials) is a config swap behind the same interface.

Poll behavior is configured per bank via the channel config JSON
(``sandbox_behavior``: ``ack`` | ``reject`` | ``slow``) and is deterministic:
the ``slow`` counter is derived from the persisted submission-event chain
(prior ``status_poll`` events for the external_ref), never from module state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from app.core.ids import new_uuid7
from app.models import (
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatorySubmissionEvent,
)
from app.services.regulatory_reporting.channels.base import SubmissionPollStatus
from app.services.regulatory_reporting.channels.errors import (
    ChannelDowntimeError,
    ChannelPreconditionError,
)

type SandboxBehavior = Literal["ack", "reject", "decline", "slow"]

SANDBOX_PREFIX = "SANDBOX-"
SANDBOX_NOTE = (
    "ORASS API is not publicly documented; this is a simulation seam — "
    "see docs/research/bog_orass_submission_channels.md"
)
SANDBOX_BEHAVIORS: tuple[SandboxBehavior, ...] = ("ack", "reject", "decline", "slow")
# Resubmission requests are auto-decided by the sandbox per config
# (resubmission_behavior: grant | deny; default grant).
RESUBMISSION_BEHAVIORS = ("grant", "deny")
# 'slow' answers pending this many times before acknowledging.
SLOW_PENDING_POLLS = 2
# Statuses a channel may deliver from: 'approved' is the normal path,
# 'submitted' is the BG/FMD/2026/07 re-upload of a downtime email submission.
_SUBMITTABLE_STATUSES = ("approved", "submitted")

# A representative BoG-style server-side validation rejection. The rule name
# and wording are OURS (marked simulated) — the real ORASS rejection semantics
# are UNKNOWN in the public record; only the existence of 400+ validation
# rules is reported (research §2.1).
_REJECT_MESSAGE = (
    "SANDBOX simulated rejection — return failed server-side validation: "
    "rule SIM-LQ-104: reported total does not cross-foot with its component "
    "rows (tolerance GHS 0.01). Correct the return and resubmit a superseding "
    "package version. [Simulated message; real ORASS rejection semantics are "
    "not public.]"
)
# Rejected = returned for correction; Declined = final refusal (LRT guide §5).
_DECLINE_MESSAGE = (
    "SANDBOX simulated decline — the regulator declined this submission as "
    "final: the request is not approvable in its current form. A new return "
    "requires a fresh submission cycle. [Simulated message; wording is ours.]"
)


def sandbox_marker() -> dict[str, Any]:
    """The labeling block that MUST accompany every sandbox detail payload."""
    return {"sandbox": True, "note": SANDBOX_NOTE}


class OrassSandboxChannel:
    """Deterministic ORASS simulator behind the SubmissionChannel protocol.

    ``prior_events`` is the package's persisted submission-event chain (any
    order); it is the only state the simulator reads, so identical inputs
    always produce identical outputs.
    """

    channel_code = "orass_sandbox"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        prior_events: Sequence[RegulatorySubmissionEvent] = (),
    ) -> None:
        self._config = dict(config or {})
        self._prior_events = tuple(prior_events)
        #: Detail payload for the most recent submit(); consumed by the
        #: workflow when recording the submission event.
        self.last_detail: dict[str, Any] = {}

    @property
    def behavior(self) -> SandboxBehavior:
        value = self._config.get("sandbox_behavior", "ack")
        return value if value in SANDBOX_BEHAVIORS else "ack"

    def submit(
        self,
        package: RegulatoryPackage,
        artifacts: Sequence[RegulatoryPackageArtifact],
    ) -> str:
        if self._config.get("downtime"):
            raise ChannelDowntimeError(
                "The ORASS sandbox is configured as DOWN. Per BoG Notice "
                "BG/FMD/2026/07, submit via the email fallback channel now and "
                "re-upload through ORASS once system functionality is restored "
                "for the submission to be deemed complete.",
                internal_detail="channel config downtime=true",
            )
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
        external_ref = self._reference(package)
        self.last_detail = {
            **sandbox_marker(),
            "behavior": self.behavior,
            "response": (
                f"SANDBOX-RECEIVED-{package.return_code}-{package.reporting_date.isoformat()}"
            ),
            "artifact_kinds": sorted(artifact.kind for artifact in artifacts),
        }
        return external_ref

    def _reference(self, package: RegulatoryPackage) -> str:
        """ORASS-style form-set reference: prefix + zero-padded sequence.

        Real ORASS references look like ``PS01390`` (form-set initials + a
        sequence). The workflow injects the per-(bank, return) submission
        sequence as ``_submission_sequence``; without it we fall back to a
        random suffix so references stay unique. The sandbox marker in every
        detail payload keeps the simulation labeled regardless of the shape.
        """
        prefix = "".join(ch for ch in package.return_code if ch.isalnum())[:4].upper()
        sequence = self._config.get("_submission_sequence")
        if isinstance(sequence, int) and sequence > 0:
            return f"{prefix}{sequence:05d}"
        return f"{prefix}-{new_uuid7().hex[:12].upper()}"

    def poll(self, external_ref: str) -> SubmissionPollStatus:
        status, _ = self.poll_with_detail(external_ref)
        return status

    def poll_with_detail(self, external_ref: str) -> tuple[SubmissionPollStatus, dict[str, Any]]:
        """Poll plus the sandbox-labeled detail payload for the event row.

        The poll number is derived from prior persisted ``status_poll`` events
        for this external_ref — deterministic, no module/instance state.
        """
        prior_polls = sum(
            1
            for event in self._prior_events
            if event.event == "status_poll" and event.external_ref == external_ref
        )
        detail: dict[str, Any] = {
            **sandbox_marker(),
            "behavior": self.behavior,
            "poll_number": prior_polls + 1,
        }
        suffix = external_ref.removeprefix(SANDBOX_PREFIX)
        if self.behavior == "reject":
            detail["response"] = f"SANDBOX-REJECTED-{suffix}"
            detail["message"] = _REJECT_MESSAGE
            detail["comments"] = _REJECT_MESSAGE
            return "rejected", detail
        if self.behavior == "decline":
            detail["response"] = f"SANDBOX-DECLINED-{suffix}"
            detail["message"] = _DECLINE_MESSAGE
            detail["comments"] = _DECLINE_MESSAGE
            return "declined", detail
        if self.behavior == "slow" and prior_polls < SLOW_PENDING_POLLS:
            detail["response"] = f"SANDBOX-PENDING-{suffix}"
            detail["message"] = "SANDBOX simulated queue: the submission is still processing."
            return "pending", detail
        detail["response"] = f"SANDBOX-ACK-{suffix}"
        detail["message"] = "SANDBOX simulated acknowledgement: submission received and validated."
        return "acknowledged", detail

    def decide_resubmission(self, external_ref: str, reason: str) -> tuple[str, dict[str, Any]]:
        """Auto-decide a resubmission request per ``resubmission_behavior``.

        Mirrors ORASS: "Resubmission requests may be granted automatically or
        may require review by your Regulator, based on the reasons you
        provide" (LRT guide §5.3). The sandbox decides immediately and labels
        the decision simulated.
        """
        behavior = self._config.get("resubmission_behavior", "grant")
        granted = behavior != "deny"
        detail: dict[str, Any] = {
            **sandbox_marker(),
            "reason": reason,
            "external_ref": external_ref,
            "message": (
                "SANDBOX simulated resubmission grant: the return is available "
                "for correction; the next submission carries revision +0.1."
                if granted
                else "SANDBOX simulated resubmission denial: the stated reason "
                "was not accepted. [Simulated message; wording is ours.]"
            ),
        }
        return ("granted" if granted else "denied"), detail
