"""Synthetic Bank of Ghana staff (SPEC Part 4) for tests and seeded demonstration worlds.

Fictional people only. Emails use the reserved ``.example`` domain. They are never
offered by the gateway's identity provider: nobody can sign in as one. Tests and the
seed tooling mint their ID tokens directly with :func:`app.idp.tokens.mint_id_token`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DevStaff:
    subject: str
    name: str
    given_name: str | None
    family_name: str | None
    email: str
    groups: tuple[str, ...]
    title: str
    mfa: bool = True


DEV_STAFF: tuple[DevStaff, ...] = (
    DevStaff(
        "kowusu",
        "Kofi Owusu",
        "Kofi",
        "Owusu",
        "kofi.owusu@bog.example",
        ("srg-senior-supervisors",),
        "Senior Supervisor",
    ),
    DevStaff(
        "aboateng",
        "Akosua Boateng",
        "Akosua",
        "Boateng",
        "akosua.boateng@bog.example",
        ("srg-supervisors",),
        "Supervisor",
    ),
    DevStaff(
        "easante",
        "Esi Asante",
        "Esi",
        "Asante",
        "esi.asante@bog.example",
        ("srg-returns-policy",),
        "Returns Policy Officer",
    ),
    DevStaff(
        "madjei",
        "Michael Adjei",
        "Michael",
        "Adjei",
        "michael.adjei@bog.example",
        ("srg-returns-policy", "srg-access-approvers"),
        "Returns Policy Officer and Access Approver",
    ),
    DevStaff(
        "internal-audit",
        "Internal Audit",
        None,
        None,
        "internal.audit@bog.example",
        ("srg-internal-audit",),
        "Internal Audit",
    ),
)


def find_dev_staff(subject: str) -> DevStaff | None:
    for staff in DEV_STAFF:
        if staff.subject == subject:
            return staff
    return None
