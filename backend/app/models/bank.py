"""Banks: the institution every bank-scoped table keys on, by its ``BK-`` platform id."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TableArgs, TimestampMixin
from app.services.public_ids import new_bank_public_id


class Bank(TimestampMixin, Base):
    __tablename__ = "banks"
    __table_args__: TableArgs = (
        Index("ix_banks_organization_id", "organization_id"),
        Index(
            "uq_banks_storage_slug",
            "storage_slug",
            unique=True,
            postgresql_where=sql_text("storage_slug IS NOT NULL"),
            sqlite_where=sql_text("storage_slug IS NOT NULL"),
        ),
        UniqueConstraint("id", "organization_id", name="uq_banks_id_organization_id"),
    )

    # THE institution identifier (BK-XXXXXXXX): platform-generated at creation
    # for every bank — sandbox and real tenants alike — and used everywhere
    # (primary key, API paths, UI, integrations). One identity, no aliases.
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_bank_public_id)
    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Both are REQUIRED at creation and carry no default. Independent defaults
    # ("GHS" and "GH") were a multi-country trap: they could silently disagree,
    # so a bank created with jurisdiction_code="NG" kept reporting in cedis.
    # The reporting currency belongs to the jurisdiction — resolve it from the
    # registry (jurisdictions.currency_code) at the creation site rather than
    # defaulting here, where the registry is not reachable.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(8), ForeignKey("jurisdictions.code"), nullable=False
    )
    license_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # THE typed institution discriminator (docs/sdi.md §1): the authoritative
    # licence class every future SDI scoping keys off. FK into the global
    # ``institution_types`` registry, from which the coarse ``institution_class``
    # ('bank'|'sdi'), return family, capital regime and limits resolve — the way
    # ``jurisdiction_code`` resolves country identity. REQUIRED with NO default,
    # by the same fail-loud discipline as ``currency``/``jurisdiction_code``: an
    # unset value means the creation site skipped a required decision, not that
    # the bank is a universal bank. Distinct from the free-text
    # ``InstitutionProfile.institution_type`` master-data field — THIS is the
    # load-bearing, typed, branch-on-me field; the profile string is descriptive.
    institution_type: Mapped[str] = mapped_column(
        String(40), ForeignKey("institution_types.type_code"), nullable=False
    )
    # DNS-safe identifier used in storage bucket names
    # (aequoros-{env}-{storage_slug}-{tier}); assigned on first ingestion.
    storage_slug: Mapped[str | None] = mapped_column(String(63), nullable=True)
