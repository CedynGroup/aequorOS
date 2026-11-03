"""``remittance_flows`` — foreign remittances by corridor / recipient / channel
(feeds BSD17 Foreign Inward Remittances: SHEET 1 by recipient class, SHEET 2 by
sending region, both in US$).

Grain follows docs/remittance_scoping.md: monthly aggregates per (direction,
corridor_country, recipient_class, channel, currency). **One reporting month
per push** (batch ``as_of_date`` = month-end; BSD17 reads the latest batch on/
before the period end). ``amount_usd`` is the bank's own US$ equivalent (the
return is in US$; the platform never invents a rate); ``amount_ghs`` the cedi
equivalent; ``region`` is the sheet-2 roll-up the bank assigns (the ISO →
region table is in the dataset doc).
"""

from __future__ import annotations

from . import ReferenceSchema, register

DIRECTIONS: tuple[str, ...] = ("inbound", "outbound")
#: BSD17 SHEET 1 recipient classes (official rows 8–13)
RECIPIENT_CLASSES: tuple[str, ...] = (
    "individual",
    "exporter",
    "service_provider",
    "ngo",
    "embassy",
    "other",
)
#: BSD17 SHEET 2 sending regions (official rows 6–11)
REGIONS: tuple[str, ...] = ("uk", "usa_canada", "eu", "ecowas", "rest_of_africa", "other")
CHANNELS: tuple[str, ...] = ("bank", "mto", "mobile_money", "other")

SCHEMA = register(
    ReferenceSchema(
        kind="remittance_flows",
        description=(
            "Foreign remittance flows: monthly aggregate per direction × corridor country × "
            "recipient class × channel × currency, with US$ and cedi equivalents"
        ),
        grain=(
            "one row per (month, direction, corridor_country, recipient_class, channel, "
            "currency); one month per push (as_of_date = month-end)"
        ),
        required=(
            "month",
            "direction",
            "corridor_country",
            "region",
            "recipient_class",
            "channel",
            "currency",
            "amount_fx",
            "amount_usd",
            "amount_ghs",
        ),
        optional=("transaction_count", "operator_name", "notes"),
        numeric=("amount_fx", "amount_usd", "amount_ghs", "transaction_count"),
        dates=("month",),
        enums={
            "direction": DIRECTIONS,
            "region": REGIONS,
            "recipient_class": RECIPIENT_CLASSES,
            "channel": CHANNELS,
        },
    )
)
