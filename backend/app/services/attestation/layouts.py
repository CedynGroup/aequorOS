"""Which signature blocks a return's artifact carries, and in what order.

One authority, imported by ``pdf_signing``, ``placements``, ``artifact_signing``,
``policy``, ``signing`` and the exporters. It holds no database access, no
settings and no imports from the rest of the attestation package, so nothing here
can close an import cycle — which is the reason it is its own module rather than
three constants in ``pdf_signing``.

**Why an ORDER and not a set.** A field's ``/Lock`` is written once, in the
revision that creates the field, because the preparer then certifies the document
at ``MDPPerm.FILL_FORMS`` and editing a ``/Lock`` afterwards would itself be the
structural change that certification convicts. The locks therefore have to encode
the whole ceremony up front:

    lock(r_i) = EXCLUDE(every field of r_{i+1} … r_n)   for i < n
    lock(r_n) = ALL                                      for the final signer

Each signer seals its own block and everything signed before it, and leaves only
later signers' fields fillable. That is why the order is data a package is
prepared with, not a preference: signing out of order means a later signature
fills a field an earlier signature's ``/Lock`` already sealed, and pyHanko's diff
analysis convicts the earlier signature. Verified by execution, not by reading —
see ``tests/services/test_attestation_three_signer_chain.py``.

**Two layouts, not a flag per family.** ``standard`` is every return that exists
today: two blocks, ``(preparer, approver)``, fixed regardless of what the signing
policy names — both fields are created on every standard return now, and a policy
naming only a preparer still gets both. That is deliberately preserved rather
than "fixed": changing it would re-place fields on returns already laid out.

``icaap`` is the ICAAP filing report, whose attestation page is drawn by this
platform rather than transcribed from a regulator's workbook, so it can carry a
third block. Its order IS policy-derived: the Board slot ships **disabled**
(D-043 — nothing in the recovered BoG text requires the Board to e-sign the filed
PDF), so the default ICAAP order is the same two roles as everything else, and a
bank that turns the slot on in Settings gets a three-signer chain.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, Protocol, runtime_checkable

#: The artifact layouts a return can be rendered and signed under.
type ArtifactLayout = Literal["standard", "icaap"]

#: Canonical rank. A signing order is always a prefix-respecting subsequence of
#: this tuple, so "who signs last" is never a function of the order a JSON array
#: happened to list slots in — an administrator cannot make the Board sign first
#: by reordering the policy payload.
SIGNING_ORDER: Final[tuple[str, ...]] = ("preparer", "approver", "board")

#: What every return filed before ICAAP uses, and what every non-ICAAP return
#: keeps using. Not policy-derived: both fields exist on the artifact today even
#: under a preparer-only policy, and the redesign must not change that.
STANDARD_SIGNING_ORDER: Final[tuple[str, ...]] = ("preparer", "approver")

#: The roles a layout can carry a field for. ``icaap`` lists all three because
#: the block can exist; whether it is REQUIRED is the policy's answer, not this
#: table's.
LAYOUT_ROLES: Final[Mapping[str, tuple[str, ...]]] = {
    "standard": STANDARD_SIGNING_ORDER,
    "icaap": SIGNING_ORDER,
}

#: Only the ICAAP family renders its own attestation page. Every other family —
#: including the official BoG BSD forms, whose page comes from the regulator's
#: own workbook — is ``standard``, and a family added later is ``standard``
#: until someone draws it a third signing block.
_LAYOUT_BY_FAMILY: Final[Mapping[str, ArtifactLayout]] = {"icaap": "icaap"}


@runtime_checkable
class PolicyLike(Protocol):
    """The one thing a signing order needs from a policy: does it want this role.

    Structural rather than an import of ``policy.SigningPolicy``: ``policy``
    imports this module for :func:`signing_order_for_layout`, so naming the
    concrete class here would be a cycle.
    """

    def slot_for(self, role: str) -> object | None: ...


def layout_for_family(return_family: str) -> ArtifactLayout:
    """The artifact layout a family's returns are rendered and signed under."""
    return _LAYOUT_BY_FAMILY.get(return_family, "standard")


def signing_order_for_layout(layout: str, policy: PolicyLike | None) -> tuple[str, ...]:
    """The ceremony this artifact's fields will be created for.

    ``standard`` ignores the policy entirely (see the module docstring). For
    ``icaap`` the preparer is unconditional — somebody has to certify the
    document before anyone can fill a field in it — and each later role joins
    only if the policy in force actually names it, in canonical rank.

    A ``None`` policy means "the layout's own maximum", which is what a caller
    that is describing the artifact rather than one package asks for.
    """
    roles = LAYOUT_ROLES.get(layout, STANDARD_SIGNING_ORDER)
    if layout == "standard":
        return STANDARD_SIGNING_ORDER
    if policy is None:
        return roles
    return ("preparer",) + tuple(
        role for role in roles[1:] if policy.slot_for(role) is not None
    )


def signing_order_for(return_family: str, policy: PolicyLike | None) -> tuple[str, ...]:
    """:func:`signing_order_for_layout`, resolved through the family."""
    return signing_order_for_layout(layout_for_family(return_family), policy)


def rank(signing_order: tuple[str, ...]) -> dict[str, int]:
    """``role -> position`` in the ceremony, for the lock chain's comparisons."""
    return {role: index for index, role in enumerate(signing_order)}


__all__ = [
    "LAYOUT_ROLES",
    "SIGNING_ORDER",
    "STANDARD_SIGNING_ORDER",
    "ArtifactLayout",
    "PolicyLike",
    "layout_for_family",
    "rank",
    "signing_order_for",
    "signing_order_for_layout",
]
