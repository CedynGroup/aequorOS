"""Catalogue members that expose counterparty identity."""

IDENTITY_MEMBERS = frozenset(
    {
        "counterparty.id",
        "counterparty.name",
        "counterparty.source_reference",
        "counterparty.group",
    }
)
