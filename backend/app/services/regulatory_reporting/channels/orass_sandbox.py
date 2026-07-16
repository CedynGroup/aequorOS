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

type SandboxBehavior = Literal["ack", "reject", "slow"]

SANDBOX_PREFIX = "SANDBOX-"
SANDBOX_NOTE = (
    "ORASS API is not publicly documented; this is a simulation seam — "
    "see docs/research/bog_orass_submission_channels.md"
)
SANDBOX_BEHAVIORS: tuple[SandboxBehavior, ...] = ("ack", "reject", "slow")
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
        external_ref = f"SANDBOX-ORASS-{package.return_code}-{new_uuid7().hex[:12]}"
        self.last_detail = {
            **sandbox_marker(),
            "behavior": self.behavior,
            "response": (
                f"SANDBOX-RECEIVED-{package.return_code}-{package.reporting_date.isoformat()}"
            ),
            "artifact_kinds": sorted(artifact.kind for artifact in artifacts),
        }
        return external_ref

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
            return "rejected", detail
        if self.behavior == "slow" and prior_polls < SLOW_PENDING_POLLS:
            detail["response"] = f"SANDBOX-PENDING-{suffix}"
            detail["message"] = "SANDBOX simulated queue: the submission is still processing."
            return "pending", detail
        detail["response"] = f"SANDBOX-ACK-{suffix}"
        detail["message"] = "SANDBOX simulated acknowledgement: submission received and validated."
        return "acknowledged", detail
