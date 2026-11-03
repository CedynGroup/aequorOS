"""``atm_operations`` — monthly ATM / card operations per terminal (feeds BSD16
``MONTHLY ATM OPERATIONS``: Station / Branch · No. of Cards Issued · Minimum
withdrawal made ¢ · Maximum withdrawal made ¢, one official row per ATM).

One row per (month, atm_id). **One reporting month per push**: the batch's
``as_of_date`` is that month's end and BSD16 for a period reads the latest
batch on/before the period end, so a batch must carry exactly one month (a
multi-month file would be read as one month). Amounts are cedis (the sheet's
¢'Million convention is applied on export); ``cards_issued`` is a count.
"""

from __future__ import annotations

from . import ReferenceSchema, register

SCHEMA = register(
    ReferenceSchema(
        kind="atm_operations",
        description=(
            "Monthly ATM / card operations per terminal (station, cards issued, minimum and "
            "maximum withdrawal in the month; one reporting month per push)"
        ),
        grain="one row per (month, atm_id); one month per push (as_of_date = month-end)",
        required=(
            "month",
            "atm_id",
            "station",
            "cards_issued",
            "min_withdrawal_ghs",
            "max_withdrawal_ghs",
        ),
        optional=(
            "region",
            "branch_code",
            "cards_active",
            "txn_count",
            "txn_value_ghs",
            "cash_dispensed_ghs",
            "downtime_hours",
            "notes",
        ),
        numeric=(
            "cards_issued",
            "cards_active",
            "min_withdrawal_ghs",
            "max_withdrawal_ghs",
            "txn_count",
            "txn_value_ghs",
            "cash_dispensed_ghs",
            "downtime_hours",
        ),
        dates=("month",),
    )
)
