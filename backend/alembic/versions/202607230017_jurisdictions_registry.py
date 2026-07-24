"""Jurisdiction registry: country → currency / locale / central bank, as data.

Creates the global ``jurisdictions`` reference table (NOT tenant-scoped — shared
reference data like parameter defaults, no RLS), seeds the four launch-market
rows with public factual data, and adds the FK from ``banks.jurisdiction_code``
so every bank resolves its country, reporting currency, display locale, and
regulator dynamically. Existing banks already carry ``jurisdiction_code='GH'``
(the model default), so Sample Bank Ltd links to Ghana by this FK with no data
rewrite — the registry is what turns that code into "Bank of Ghana" and "GHS"
at read time instead of hardcoded literals.

Deadline rules, return templates, and parameter packs stay where they are
(reporting config + jurisdiction-keyed parameter tables); this table carries
identity only.

Revision ID: 202607230017
Revises: 202607200016
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607230017"
down_revision = "202607200016"
branch_labels = None
depends_on = None

TABLE = "jurisdictions"
FK = "fk_banks_jurisdiction_code_jurisdictions"

SEED_ROWS = [
    # code, country, ccy, ccy name, locale, central bank, short, portal, tz
    ("GH", "Ghana", "GHS", "Ghana Cedi", "en-GH", "Bank of Ghana", "BoG",
     "ORASS", "Africa/Accra"),
    ("NG", "Nigeria", "NGN", "Nigerian Naira", "en-NG", "Central Bank of Nigeria",
     "CBN", None, "Africa/Lagos"),
    ("KE", "Kenya", "KES", "Kenyan Shilling", "en-KE", "Central Bank of Kenya",
     "CBK", None, "Africa/Nairobi"),
    ("ZA", "South Africa", "ZAR", "South African Rand", "en-ZA",
     "South African Reserve Bank", "SARB", None, "Africa/Johannesburg"),
]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("country_name", sa.String(length=80), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("currency_name", sa.String(length=60), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("central_bank_name", sa.String(length=120), nullable=False),
        sa.Column("regulator_short", sa.String(length=16), nullable=False),
        sa.Column("submission_portal", sa.String(length=60), nullable=True),
        sa.Column("timezone", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    insert = sa.text(
        f"INSERT INTO {TABLE} (code, country_name, currency_code, currency_name, "
        "locale, central_bank_name, regulator_short, submission_portal, timezone, "
        "created_at, updated_at) VALUES (:code, :country, :ccy, :ccy_name, :locale, "
        ":cb, :short, :portal, :tz, now(), now())"
    )
    connection = op.get_bind()
    for code, country, ccy, ccy_name, locale, cb, short, portal, tz in SEED_ROWS:
        connection.execute(
            insert,
            {
                "code": code, "country": country, "ccy": ccy, "ccy_name": ccy_name,
                "locale": locale, "cb": cb, "short": short, "portal": portal, "tz": tz,
            },
        )
    op.create_foreign_key(FK, "banks", TABLE, ["jurisdiction_code"], ["code"])


def downgrade() -> None:
    op.drop_constraint(FK, "banks", type_="foreignkey")
    op.drop_table(TABLE)
