"""READ-ONLY: which tenants would start failing submission once C-6 is enforced.

``return_signing_policies.required_attachments`` has been writable since the
attestation spine landed and **nothing has ever counted it**, so a bank that
configured "file the Board resolution with this return" has been submitting
without one and has never been told. ICAAP P3 makes the requirement real.

That is a correction, not a regression — but it must not be discovered by a
bank on a filing deadline. This script answers, before the change deploys:

1. **Active policies** that name at least one required attachment.
2. **Would-block packages**: non-terminal packages (``generated`` |
   ``validated`` | ``pending_approval`` | ``approved``) whose resolved policy
   names attachments and which have none attached. These are the filings that
   would refuse the day enforcement lands.
3. **Informational**: already-submitted packages under such a policy. Nothing
   is retroactive; they are listed so the scale of the historical gap is
   visible rather than inferred.

Gate: zero rows in (1) and (2) -> enforce. Otherwise each row needs a decision
before deploy (the bank relaxes the requirement through the audited settings
PUT, or uploads the document through the new endpoint).

Run it against the primary with the BYPASSRLS worker URL, which is the only
credential that can see every tenant::

    cd backend
    RUN_INPROCESS_WORKER=0 LIVE_DATA_DATABASE_URL="$WORKER_DATABASE_URL" \\
      uv run python scripts/audit_required_attachment_policies.py \\
      > ../.ai/icaap/test-results/required-attachment-audit.txt

**The session is server-side read-only** (``SET SESSION CHARACTERISTICS AS
TRANSACTION READ ONLY``, the same guard ``tests/live_data`` uses), so this
cannot mutate what it certifies even if a future edit to it tried to. The
output names tenants, so it belongs in the gitignored results directory.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

NON_TERMINAL = ("generated", "validated", "pending_approval", "approved")
SUBMITTED = ("submitted", "acknowledged")


def _engine() -> Engine:
    url = os.environ.get("LIVE_DATA_DATABASE_URL") or os.environ.get("WORKER_DATABASE_URL")
    if not url:
        sys.exit(
            "Set LIVE_DATA_DATABASE_URL (or WORKER_DATABASE_URL) to the BYPASSRLS "
            "worker URL. This script reads every tenant and writes nothing."
        )
    # psycopg v3 is what is installed; a bare postgresql:// URL selects the
    # absent psycopg2 driver and fails at connect time.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, future=True)


_POLICY_SQL = text(
    """
    SELECT id,
           organization_id,
           bank_id,
           return_code,
           return_family,
           basis,
           required_attachments,
           require_signature,
           effective_from,
           effective_to
      FROM return_signing_policies
     WHERE required_attachments IS NOT NULL
       AND json_array_length(required_attachments::json) > 0
       AND (effective_to IS NULL OR effective_to >= :today)
     ORDER BY organization_id, return_family, return_code
    """
)

#: Packages a policy row could apply to. Resolution order (code beats family,
#: bank beats org) is re-applied in Python below, against the SAME precedence
#: the service uses -- see ``attestation/policy.resolve_policy``.
#:
#: The attachment count is spliced in only when the attachment table EXISTS. The
#: whole point of this audit is to run BEFORE the ICAAP filing migration is
#: applied, on a primary that therefore has no such table -- and on which, by
#: definition, no package has an attachment. Querying it unconditionally made
#: the audit impossible to run at the one moment it is for.
_PACKAGE_SELECT = """
    SELECT p.id,
           p.organization_id,
           p.bank_id,
           p.return_code,
           p.return_family,
           p.basis,
           p.reporting_date,
           p.status,
           {attachments} AS active_attachments
      FROM regulatory_packages p
     WHERE p.status = ANY(:statuses)
     ORDER BY p.organization_id, p.return_code, p.reporting_date
"""
_ATTACHMENT_COUNT = """(SELECT count(*)
              FROM regulatory_package_attachments a
             WHERE a.package_id = p.id
               AND NOT EXISTS (
                     SELECT 1
                       FROM regulatory_package_attachment_withdrawals w
                      WHERE w.attachment_id = a.id))"""


def _package_sql(*, attachments_exist: bool):
    return text(
        _PACKAGE_SELECT.format(
            attachments=_ATTACHMENT_COUNT if attachments_exist else "0"
        )
    )


def _applies(  # noqa: PLR0911 - one return per resolution dimension
    policy: dict[str, Any], package: dict[str, Any]
) -> bool:
    if policy["organization_id"] != package["organization_id"]:
        return False
    if policy["bank_id"] is not None and policy["bank_id"] != package["bank_id"]:
        return False
    if policy["return_code"] is not None:
        if policy["return_code"] != package["return_code"]:
            return False
    elif policy["return_family"] is not None and (
        policy["return_family"] != package["return_family"]
    ):
        return False
    if policy["basis"] is not None and policy["basis"] != package["basis"]:
        return False
    effective_from = policy["effective_from"]
    effective_to = policy["effective_to"]
    reporting_date = package["reporting_date"]
    if effective_from is not None and reporting_date < effective_from:
        return False
    return not (effective_to is not None and reporting_date > effective_to)


def _specificity(policy: dict[str, Any]) -> tuple[int, int, int]:
    """More specific wins, exactly as ``resolve_policy`` orders it."""
    return (
        1 if policy["bank_id"] is not None else 0,
        1 if policy["return_code"] is not None else 0,
        1 if policy["basis"] is not None else 0,
    )


def _required(policy: dict[str, Any]) -> list[str]:
    raw = policy["required_attachments"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [str(entry) for entry in (raw or [])]


def main() -> int:
    engine = _engine()
    today = date.today()
    with engine.connect() as connection:
        # Server-side read-only for the whole session: this script certifies the
        # primary and must be incapable of changing it.
        connection.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        attachments_exist = (
            connection.execute(
                text("SELECT to_regclass('public.regulatory_package_attachments')")
            ).scalar()
            is not None
        )
        package_sql = _package_sql(attachments_exist=attachments_exist)
        policies = [
            dict(row) for row in connection.execute(_POLICY_SQL, {"today": today}).mappings()
        ]
        open_packages = [
            dict(row)
            for row in connection.execute(
                package_sql, {"statuses": list(NON_TERMINAL)}
            ).mappings()
        ]
        filed_packages = [
            dict(row)
            for row in connection.execute(package_sql, {"statuses": list(SUBMITTED)}).mappings()
        ]

    print(
        "attachment tables present: "
        f"{'yes' if attachments_exist else 'no (pre-migration primary)'}"
    )
    print()
    print("=== (1) Active policies that require an attachment ===")
    if not policies:
        print("none")
    for policy in policies:
        print(
            f"org={policy['organization_id']} bank={policy['bank_id'] or '*'} "
            f"code={policy['return_code'] or '*'} family={policy['return_family'] or '*'} "
            f"basis={policy['basis'] or '*'} requires={_required(policy)} "
            f"effective={policy['effective_from']}..{policy['effective_to'] or 'open'}"
        )

    def _blocked(packages: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for package in packages:
            candidates = [p for p in policies if _applies(p, package)]
            if not candidates:
                continue
            winner = max(candidates, key=_specificity)
            needed = _required(winner)
            if needed and package["active_attachments"] < len(set(needed)):
                counts[(package["organization_id"], package["return_code"])] += 1
        return counts

    print()
    print("=== (2) Packages that WOULD BLOCK on submission once enforced ===")
    would_block = _blocked(open_packages)
    if not would_block:
        print("none")
    for (org, code), count in sorted(would_block.items()):
        print(f"org={org} return={code} packages={count}")

    print()
    print("=== (3) Informational: already-submitted packages under such a policy ===")
    historical = _blocked(filed_packages)
    if not historical:
        print("none")
    for (org, code), count in sorted(historical.items()):
        print(f"org={org} return={code} packages={count}")

    print()
    gate_clear = not policies and not would_block
    print(
        "GATE: clear — no tenant is affected; enforcement can deploy."
        if gate_clear
        else "GATE: NOT clear — each row above needs a decision before deploy."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
