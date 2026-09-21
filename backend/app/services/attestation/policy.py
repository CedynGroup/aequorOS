"""Who must sign which return — configuration, never hardcoded.

Signing is REQUIRED by default (see :func:`default_policy`), with two
deployment switches, both applied AFTER resolution
(:func:`_apply_deployment_switches`) so they override configured mandatory rows
as well as the built-in default:

``ATTESTATION_ESIGN_REQUIRED=0``
    The deployment-wide kill-switch (:func:`_apply_esign_kill_switch`): NO
    return — configured row or default — demands a signature.

``ICAAP_SIGNING_ENABLED=NO`` (the default)
    The same thing scoped to the ``icaap`` family alone
    (:func:`_apply_icaap_signing_switch`), so the ICAAP report — Board slot
    included — is signed only where the deployment says so, exactly as the
    prudential returns' ceremony is controlled by ``ATTESTATION_SIGNING_ENABLED``.

Neither deletes anything. A suspended policy keeps its slots, its
``policy_id`` and its row; only ``require_signature`` and ``require_signed_pdf``
are forced false, and the ``source`` says WHICH switch did it, so flipping the
switch back restores the untouched resolution result.

What stays configurable is everything the Bank of Ghana has not confirmed
(docs/attestation_esignature.md §8): whether a signed PDF artifact is accepted
as the filing (C1), exactly which officers must sign which of the thirteen
returns (C2), how the daily return is handled (C3), and what ICAAP's board
attestation demands (C4). This module is the reason none of that is baked into
code: every one of them is a row, changeable without a release.

Resolution is most-specific-first, so "the CFO signs BSD-2 but the Head of
Finance signs BSD-3, and Bank X differs from Bank Y" is expressible without a
release:

    (bank, return_code, basis) → (bank, return_code) → (bank, family)
        → (org, return_code) → (org, family) → the built-in default

The policy in force is resolved **as at the reporting date**, so a later policy
change never retroactively invalidates a filed return.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import ReturnSigningPolicy
from app.services.attestation import layouts


@dataclass(frozen=True)
class SignatureSlot:
    """One required signature: a role, optionally constrained to officer titles."""

    role: str
    min_count: int = 1
    officer_titles: tuple[str, ...] = ()

    def accepts_title(self, job_title: str | None) -> bool:
        """True when the signer's title satisfies this slot.

        An empty ``officer_titles`` means the slot is role-only — which is the
        default until C2 is confirmed, deliberately: enforcing a guessed
        officer title would block a legitimate signer.
        """
        if not self.officer_titles:
            return True
        if not job_title:
            return False
        normalised = job_title.strip().casefold()
        return any(title.strip().casefold() == normalised for title in self.officer_titles)


@dataclass(frozen=True)
class SigningPolicy:
    """The effective signing requirement for one package."""

    slots: tuple[SignatureSlot, ...]
    require_signature: bool = True
    require_signed_pdf: bool = False
    distinct_signers: bool = True
    required_attachments: tuple[str, ...] = ()
    #: Signatures must arrive in canonical order. Additive and defaulted False, so
    #: every policy written before it existed means exactly what it meant then.
    ordered_slots: bool = False
    source: str = "default"
    policy_id: str | None = None

    @property
    def total_required(self) -> int:
        return sum(slot.min_count for slot in self.slots)

    def slot_for(self, role: str) -> SignatureSlot | None:
        return next((slot for slot in self.slots if slot.role == role), None)

    def field_roles(self, layout: str) -> tuple[str, ...]:
        """The ceremony this policy produces on that artifact layout."""
        return layouts.signing_order_for_layout(layout, self)

    def enforces_order(self, layout: str) -> bool:
        """Whether the signatures must arrive in canonical order.

        ``ordered_slots`` is the administrator's answer. The second clause is not
        one: a document carrying more than two signature fields has a lock chain
        in which every signer but the last seals the ones before it, so signing
        out of order produces a file whose earlier signatures a verifier convicts.
        An administrator who saved ``ordered_slots=False`` on such a return has
        asked for something that cannot be delivered, and the platform does not
        deliver it badly.
        """
        return self.ordered_slots or len(self.field_roles(layout)) > 2  # noqa: PLR2004

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "policy_id": self.policy_id,
            "require_signature": self.require_signature,
            "require_signed_pdf": self.require_signed_pdf,
            "distinct_signers": self.distinct_signers,
            "ordered_slots": self.ordered_slots,
            "required_attachments": list(self.required_attachments),
            "required_signatures": [
                {
                    "role": slot.role,
                    "min_count": slot.min_count,
                    "officer_titles": list(slot.officer_titles),
                }
                for slot in self.slots
            ],
        }


#: The maker-checker shape every policy gets if it names no slots: one preparer,
#: one approver, distinct. Role-only — no officer titles — because *which*
#: officer must sign which return is still unconfirmed with the regulator (C2),
#: and enforcing a guessed title would block a legitimate signer.
_DEFAULT_SLOTS: tuple[SignatureSlot, ...] = (
    SignatureSlot(role="preparer"),
    SignatureSlot(role="approver"),
)


#: The ICAAP filing report's default. Preparer and approver, exactly like every
#: other return — the Board slot is BUILT but off (D-043).
#:
#: The regulatory matrix says no rule requires the Board to e-sign the filed PDF:
#: ¶71 requires Board resolutions to ACCOMPANY the submission, ¶45 requires Board
#: challenge and approval, Stress ¶20 requires a Board attestation. None of those
#: is a signature on this document, and Board approval is evidenced regardless of
#: the slot — by the in-platform BRC and Board stage decisions, the challenge log,
#: and the mandatory board resolution attachment, which relaxing a signature slot
#: can never relax (D-031).
#:
#: A bank that wants the third signature turns the slot on per return in Settings,
#: which is an audited change and produces a three-signer chain on the next
#: package it certifies — not on one already prepared, since a document's fields
#: are fixed the moment its preparer signs.
_ICAAP_SLOTS: tuple[SignatureSlot, ...] = (
    SignatureSlot(role="preparer"),
    SignatureSlot(role="approver"),
)

#: Return codes whose family is ``icaap`` but which are not the filing report.
#: Named so the branch reads as a decision rather than a prefix match.
_ICAAP_DISCLOSURE_CODE = "ICAAP-DISCLOSURE"


def default_policy(return_family: str, return_code: str | None = None) -> SigningPolicy:
    """The policy when an institution has configured NONE — signature REQUIRED.

    A return leaves this platform for the regulator only after the officer who
    prepared it and a second officer who checked it have each signed the exact
    figures being filed. That is the product: an unsigned filing is not a
    lighter-weight filing, it is an unattested one.

    This is an *institutional* control, and the distinction matters. What the
    Bank of Ghana demands **on the artifact** — whether a signed PDF is accepted
    at all (C1), which officer titles must appear (C2) — is still unconfirmed,
    and none of it is baked in here: titles stay unset, and a bank that learns
    otherwise changes rows, not code. What a bank demands **of itself** before
    filing is not BoG's question to answer, and defaulting it to "nobody need
    sign" was the wrong reading of that boundary.

    The cost is real and is accepted deliberately: a return cannot be filed
    while a required signer is unavailable, including the daily DBK return
    (10:00 T+1, five days a week — gap G17). A bank that finds that
    unworkable relaxes it per return in Settings, an explicit and audited act;
    the platform no longer makes that choice silently on their behalf.

    The existing `generated_by != approver` maker-checker control on the
    approval decision is unaffected and still applies to every return.

    Two deployment exceptions exist: ``ATTESTATION_ESIGN_REQUIRED=0`` suspends
    the requirement for every family, and ``ICAAP_SIGNING_ENABLED=NO`` suspends
    it for the ICAAP family alone (:func:`_apply_deployment_switches`).
    """
    if return_family == "icaap":
        # Ordered even at two slots: an ICAAP artifact can carry a third block, so
        # the order is a property of the ceremony rather than of the slot count,
        # and turning the Board slot on must not also have to remember this.
        return SigningPolicy(
            slots=_ICAAP_SLOTS,
            require_signature=True,
            require_signed_pdf=True,
            distinct_signers=True,
            ordered_slots=True,
            source="platform_default",
        )
    _ = return_code  # only the ICAAP family distinguishes its returns today
    return SigningPolicy(
        slots=_DEFAULT_SLOTS,
        require_signature=True,
        require_signed_pdf=True,
        distinct_signers=True,
        source="platform_default",
    )


def _specificity(
    row: ReturnSigningPolicy, bank_id: str, return_code: str
) -> tuple[int, int, int, int, float]:
    """Rank a candidate row. Higher sorts first.

    The final ``created_at`` term is load-bearing, not cosmetic: two rows for
    the same scope can share an ``effective_from`` (an administrator correcting
    a policy the same day), and without a total order the winner would depend on
    database row order. A non-deterministic signing policy is unacceptable —
    it decides whether a filed return was properly attested.
    """
    return (
        1 if row.bank_id == bank_id else 0,
        1 if row.return_code == return_code else 0,
        1 if row.basis is not None else 0,
        row.effective_from.toordinal(),
        row.created_at.timestamp(),
    )


def resolve_policy(  # noqa: PLR0913 - the full package scope is the lookup key
    db: Session,
    ctx: TenantContext,
    *,
    bank_id: str,
    return_code: str,
    return_family: str,
    basis: str,
    as_at: date,
) -> SigningPolicy:
    """The policy in force for this package as at its reporting date.

    The resolution result — configured row or platform default — is subject to
    the deployment switches (:func:`_apply_deployment_switches`), applied last
    so they override configured mandatory rows too.
    """
    candidates = list(
        db.scalars(
            select(ReturnSigningPolicy).where(
                ReturnSigningPolicy.organization_id == ctx.organization_id,
                or_(
                    ReturnSigningPolicy.bank_id == bank_id,
                    ReturnSigningPolicy.bank_id.is_(None),
                ),
                or_(
                    ReturnSigningPolicy.return_code == return_code,
                    ReturnSigningPolicy.return_code.is_(None),
                ),
                or_(
                    ReturnSigningPolicy.return_family == return_family,
                    ReturnSigningPolicy.return_family.is_(None),
                ),
                or_(
                    ReturnSigningPolicy.basis == basis,
                    ReturnSigningPolicy.basis.is_(None),
                ),
                ReturnSigningPolicy.effective_from <= as_at,
                or_(
                    ReturnSigningPolicy.effective_to.is_(None),
                    ReturnSigningPolicy.effective_to >= as_at,
                ),
            )
        )
    )
    if not candidates:
        return _apply_deployment_switches(
            default_policy(return_family, return_code), return_family
        )

    winner = max(candidates, key=lambda row: _specificity(row, bank_id, return_code))
    slots = tuple(
        SignatureSlot(
            role=str(entry.get("role", "approver")),
            min_count=int(entry.get("min_count", 1)),
            officer_titles=tuple(str(t) for t in entry.get("officer_titles", ()) or ()),
        )
        for entry in (winner.required_signatures or [])
    )
    if not slots:
        slots = _DEFAULT_SLOTS
    return _apply_deployment_switches(
        SigningPolicy(
            slots=slots,
            require_signature=winner.require_signature,
            require_signed_pdf=winner.require_signed_pdf,
            distinct_signers=winner.distinct_signers,
            ordered_slots=winner.ordered_slots,
            required_attachments=tuple(str(a) for a in (winner.required_attachments or ())),
            source="configured",
            policy_id=str(winner.id),
        ),
        return_family,
    )


#: Source attributions a suspended policy carries, so a reader can tell WHICH
#: switch put it to sleep — and therefore which one to flip to wake it.
SUSPENDED_SOURCES: Final[frozenset[str]] = frozenset(
    {"esign_disabled", "icaap_signing_disabled"}
)


def _suspend(resolved: SigningPolicy, source: str) -> SigningPolicy:
    """Force the requirement off, keeping everything that says what it was.

    Slots, ``required_attachments`` and ``policy_id`` survive: they are inert
    once ``require_signature`` is false, and they are what lets the UI and the
    audit trail show what WOULD be required and which row went dormant. A policy
    that already demanded nothing is returned untouched, keeping its own
    attribution — an administrator who relaxed it themselves was not overridden
    by a switch, and claiming otherwise would misattribute their decision.
    """
    if not resolved.require_signature and not resolved.require_signed_pdf:
        return resolved
    return replace(
        resolved,
        require_signature=False,
        require_signed_pdf=False,
        source=source,
    )


def _apply_deployment_switches(resolved: SigningPolicy, return_family: str) -> SigningPolicy:
    """Both deployment switches, BROADEST FIRST.

    Order is the whole of the decision here. When the deployment-wide
    kill-switch is off, every family is suspended and flipping
    ``ICAAP_SIGNING_ENABLED`` to YES alone would change nothing — so an ICAAP
    policy must be attributed to ``esign_disabled``, the switch that actually
    has to move, rather than to the narrower one that is merely also off.
    """
    suspended = _apply_esign_kill_switch(resolved)
    if suspended.source == "esign_disabled":
        return suspended
    return _apply_icaap_signing_switch(suspended, return_family)


def _apply_icaap_signing_switch(resolved: SigningPolicy, return_family: str) -> SigningPolicy:
    """``ICAAP_SIGNING_ENABLED=NO`` means the ICAAP family demands no signature.

    The founder's switch, and the ICAAP-scoped twin of
    :func:`_apply_esign_kill_switch`: the ICAAP report — Board slot included —
    is signed only where the deployment says so, and takes the bare
    maker-checker approval path everywhere else. It ships off (D-043: nothing in
    the recovered BoG text requires the Board, or anyone, to e-sign the filed
    ICAAP PDF), and it exists so that a Board which asks for the signature after
    onboarding is one environment variable away from it rather than a release.

    Scoped to the family, never to the module: the ICAAP *workspace* has no
    on/off switch and is not getting one under another name (D-046). What is
    suspended is a ceremony, not a surface.

    Dormant, not deleted — see :func:`_suspend`. A bank that has configured a
    three-signer ICAAP policy keeps that row, keeps its ``policy_id``, and gets
    it back byte for byte the moment the switch reads YES.
    """
    if return_family != "icaap":
        return resolved
    if get_settings().icaap.signing_enabled:
        return resolved
    return _suspend(resolved, "icaap_signing_disabled")


def _apply_esign_kill_switch(resolved: SigningPolicy) -> SigningPolicy:
    """``ATTESTATION_ESIGN_REQUIRED=0`` means no return demands a signature.

    Applied AFTER resolution so it overrides configured mandatory rows as well
    as the platform default. Rows are dormant, not deleted: the moment the flag
    returns to true, the untouched resolution result is what this returns.
    Slots and ``policy_id`` are kept — they are inert once ``require_signature``
    is false, and they let the UI and audit trail show what would be required
    and which row went dormant. A policy an administrator already relaxed keeps
    its ``configured`` attribution: the flag changed nothing for it.
    """
    if get_settings().attestation.esign_required:
        return resolved
    return _suspend(resolved, "esign_disabled")
