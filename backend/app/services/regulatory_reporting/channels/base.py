"""Submission channel protocol (docs/regulatory_reporting.md §5, ``channels/base.py``).

Only the contract lives in this wave. Concrete channels — the clearly-labeled
ORASS sandbox simulator and the email/manual fallback — are delivered by the
export/submission wave as plugins behind this interface; real ORASS onboarding
is then a config swap, never a code change outside ``channels/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from app.models import RegulatoryPackage, RegulatoryPackageArtifact

type SubmissionPollStatus = Literal["pending", "acknowledged", "rejected"]


class SubmissionChannel(Protocol):
    """One way of delivering an approved package to the regulator.

    ``submit`` delivers the package's exported artifacts and returns the
    channel's external reference (receipt id, message id, tracking number).
    ``poll`` maps that reference onto the regulator-side status.
    Implementations must never mutate the package; the workflow service owns
    all state transitions and submission-event rows.
    """

    def submit(
        self,
        package: RegulatoryPackage,
        artifacts: Sequence[RegulatoryPackageArtifact],
    ) -> str: ...

    def poll(self, external_ref: str) -> SubmissionPollStatus: ...
