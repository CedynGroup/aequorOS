"""``teller_withdrawals`` — over-the-counter cash withdrawals, one row per
teller transaction (feeds BSD1A ``20 LARGEST WITHDRAWALS``: CUSTOMER · BRANCH
· TYPE OF A/C · THURSDAY … WEDNESDAY amounts in ¢ Million · TOTAL).

Grain: one row per withdrawal paid in cash across the counter (``channel``
``teller``; ATM / mobile / transfer debits are NOT over-the-counter and belong
elsewhere). **One reporting week per push**: the batch's ``as_of_date`` is the
week's reporting date and BSD1A for a reporting date reads the latest batch on
or before it, then keeps only the rows whose ``txn_date`` falls in the seven
days ending on that date. ``amount`` is in the account's ``currency``;
``amount_ghs`` is the bank's own cedi equivalent (what the return prints — the
platform never invents a conversion). ``customer_reference`` is the bank's
customer id (CIF); ``customer_name`` is what prints under CUSTOMER.
"""

from __future__ import annotations

from . import ReferenceSchema, register

#: BSD1A ``TYPE OF A/C`` vocabulary (the deposit_account_type of the debited
#: account, lower-case): current | savings | call | fixed | other.
ACCOUNT_TYPES: tuple[str, ...] = ("current", "savings", "call", "fixed", "other")
CHANNELS: tuple[str, ...] = ("teller",)

SCHEMA = register(
    ReferenceSchema(
        kind="teller_withdrawals",
        description=(
            "Over-the-counter cash withdrawals: one row per teller transaction "
            "(date, branch, customer, account type, currency, amount, cedi equivalent); "
            "one reporting week per push"
        ),
        grain=(
            "one row per over-the-counter cash withdrawal (txn_date, branch, customer, "
            "account); one week per push (as_of_date = the week's reporting date)"
        ),
        required=(
            "txn_date",
            "branch",
            "customer_reference",
            "customer_name",
            "account_type",
            "currency",
            "amount",
            "amount_ghs",
            "channel",
        ),
        optional=(
            "txn_reference",
            "account_reference",
            "branch_code",
            "teller_id",
            "txn_time",
            "notes",
        ),
        numeric=("amount", "amount_ghs"),
        dates=("txn_date",),
        enums={"account_type": ACCOUNT_TYPES, "channel": CHANNELS},
    )
)
