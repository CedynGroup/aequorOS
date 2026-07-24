from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Jurisdiction(TimestampMixin, Base):
    """Global reference registry: one row per country AequorOS operates in.

    ``banks.jurisdiction_code`` points here, and everything country-derived —
    reporting currency, display locale, the central bank / regulator identity,
    the submission portal — resolves from this row instead of hardcoded
    literals. Onboarding a new country is a data exercise: one registry row +
    a jurisdiction-keyed parameter pack + that regulator's return families
    (regulatory_reporting.md §8). Deliberately NOT tenant-scoped (shared
    reference data, like parameter defaults) and read-only through the API.
    """

    __tablename__ = "jurisdictions"

    # ISO 3166-1 alpha-2 (natural primary key; banks reference it directly).
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    country_name: Mapped[str] = mapped_column(String(80), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 4217
    currency_name: Mapped[str] = mapped_column(String(60), nullable=False)
    # BCP-47 tag driving number/date formatting in the dashboard (e.g. en-GH).
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    central_bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Short display form used across the UI (BoG, CBN, CBK, SARB).
    regulator_short: Mapped[str] = mapped_column(String(16), nullable=False)
    # Electronic submission portal, where one exists (ORASS for BoG).
    submission_portal: Mapped[str | None] = mapped_column(String(60), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(40), nullable=True)
