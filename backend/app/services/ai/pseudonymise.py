"""What a name may be replaced by, and what the validator must refuse.

Two halves of one rule. Outbound, a name becomes a KEY — ``bank``, ``regulator``
— and the model is told its ROLE, never its value. Inbound, every real name the
tenant's own registers hold is a deny term, so a model that writes one anyway is
caught even though it was never told it.

**People are never sent and have no key.** An individual's name is not
pseudonymised out of the fact sheet, it is absent from it: there is no
``{{E:chairman}}``. Roles ("the Board", "the Chief Risk Officer") are generic
prose the model may write on its own.

Resolution happens server-side only, at preview and accept time, from the
CURRENT registers. A draft written last week that is accepted today renders the
bank's name as it is today.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Bank, Organization, User
from app.models.jurisdiction import Jurisdiction
from app.services import jurisdictions as jurisdictions_service

#: key -> the ROLE the model is told. Values are resolved server-side and never
#: cross the boundary. ``sub_N`` is reserved for a subsidiary register that does
#: not exist yet, so it is never offered today.
ENTITY_ROLES: Mapping[str, str] = {
    "bank": "the reporting institution",
    "regulator": "the prudential regulator",
    "central_bank": "the central bank",
    "country": "the country of incorporation",
    "currency": "the reporting currency",
    "as_of": "the reporting date",
    "fiscal_year": "the financial year",
    "framework": "the regulatory framework this report follows",
    "basis": "the reporting basis",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://|\bwww\.", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


@dataclass(frozen=True)
class EntityMap:
    """Keys offered to the model, and the values the server substitutes."""

    values: Mapping[str, str]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))

    def offered(self) -> tuple[dict[str, str], ...]:
        """The ``entities`` list sent in the fact sheet: keys and roles only."""
        return tuple(
            {"key": key, "role": ENTITY_ROLES[key]}
            for key in sorted(self.values)
            if key in ENTITY_ROLES
        )


def _add(values: dict[str, str], key: str, value: str | None) -> None:
    if value:
        values[key] = value


def build_entity_map(  # noqa: PLR0913 - one key per entity the model may name
    db: Session,
    bank: Bank,
    *,
    as_of_label: str,
    fiscal_year_label: str,
    framework_label: str,
    basis_label: str,
) -> EntityMap:
    """The entity keys this request may use, with their server-side values."""
    values: dict[str, str] = {}
    _add(values, "bank", bank.name)
    _add(values, "regulator", jurisdictions_service.regulator_short(db, bank))
    _add(values, "central_bank", jurisdictions_service.regulator_name(db, bank))
    jurisdiction = jurisdictions_service.get_jurisdiction(db, bank)
    if jurisdiction is not None:
        _add(values, "country", jurisdiction.country_name)
    _add(values, "currency", jurisdictions_service.base_currency(bank))
    _add(values, "as_of", as_of_label)
    _add(values, "fiscal_year", fiscal_year_label)
    _add(values, "framework", framework_label)
    _add(values, "basis", basis_label)
    return EntityMap(values=values)


def tenant_deny_terms(db: Session, organization_id: str, bank: Bank) -> frozenset[str]:
    """Every real name this tenant's registers hold.

    Institution names, former names, related parties (individuals AND legal
    entities) and the organisation's own users. Outlet and product names are
    deliberately excluded: "Current Account" as a deny term would reject honest
    prose, and neither is ever sent.
    """
    terms: set[str] = set()
    for value in (bank.name, bank.short_name):
        if value:
            terms.add(value.strip())
    organization = db.get(Organization, organization_id)
    if organization is not None and organization.name:
        terms.add(organization.name.strip())
    terms.update(_previous_names(db, organization_id, bank))
    terms.update(_party_names(db, organization_id, bank))
    for display_name in db.scalars(
        select(User.display_name).where(User.organization_id == organization_id)
    ):
        if display_name:
            terms.add(display_name.strip())
    # A single short token would fire on ordinary prose; multi-word names are
    # safe at any length because the whole phrase must match.
    minimum = get_settings().ai.min_deny_term_chars
    return frozenset(
        term for term in terms if term and (" " in term or len(term) >= minimum)
    )


def _previous_names(db: Session, organization_id: str, bank: Bank) -> set[str]:
    try:
        from app.models.institution_profile import BankNameHistory  # noqa: PLC0415
    except ImportError:  # pragma: no cover - the register always exists today
        return set()
    rows = db.scalars(
        select(BankNameHistory.previous_name).where(
            BankNameHistory.organization_id == organization_id,
            BankNameHistory.bank_id == bank.id,
        )
    )
    return {value.strip() for value in rows if value}


def _party_names(db: Session, organization_id: str, bank: Bank) -> set[str]:
    try:
        from app.models.institution_profile import RelatedParty  # noqa: PLC0415
    except ImportError:  # pragma: no cover - the register always exists today
        return set()
    rows = db.scalars(
        select(RelatedParty.full_name).where(
            RelatedParty.organization_id == organization_id,
            RelatedParty.bank_id == bank.id,
        )
    )
    return {value.strip() for value in rows if value}


def jurisdiction_deny_terms(db: Session) -> frozenset[str]:
    """Country, currency and regulator names from the GLOBAL registry.

    Every row, not just this bank's: a model that writes the wrong regulator's
    name is exactly as wrong as one that writes the right one, and the fix for
    both is the same — names arrive through ``{{E:...}}`` or not at all.
    """
    terms: set[str] = set()
    for row in db.scalars(select(Jurisdiction)):
        for value in (
            row.country_name,
            row.currency_code,
            row.currency_name,
            row.central_bank_name,
            row.regulator_short,
        ):
            if value:
                terms.add(str(value).strip())
    return frozenset(term for term in terms if term)


def scrub_label(  # noqa: PLR0911 - one return per reason to drop the label
    label: str | None, deny_terms: frozenset[str]
) -> str | None:
    """A manual-table label, or None if it may not be sent.

    Manual labels are the one free-text field a user can put into a fact sheet,
    so they are the one prompt-injection channel. Dropping the fact is always an
    option: the draft loses one figure, not its integrity.
    """
    if label is None:
        return None
    text = label.strip()
    if not text:
        return None
    if len(text) > get_settings().ai.max_manual_label_chars:
        return None
    if _CONTROL_RE.search(text) or "{{" in text or "}}" in text:
        return None
    if _EMAIL_RE.search(text) or _URL_RE.search(text):
        return None
    lowered = text.casefold()
    for term in deny_terms:
        cleaned = term.strip().casefold()
        if cleaned and cleaned in lowered:
            return None
    return text


__all__ = [
    "ENTITY_ROLES",
    "EntityMap",
    "build_entity_map",
    "jurisdiction_deny_terms",
    "scrub_label",
    "tenant_deny_terms",
]
