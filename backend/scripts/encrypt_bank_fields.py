"""Explicit bank-key backfill, using the configured tenant database and workload IAM.

Run only after the additive schema migration, before enabling a protected bank.
No keys or customer values are accepted as command-line arguments or printed.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from typing import cast

from sqlalchemy.orm import Session

from app.core.key_management.backfill import backfill_bank
from app.db import session as database


class _Arguments(argparse.Namespace):
    organization_id: str
    bank_id: str
    batch_size: int
    reencrypt: bool


def main() -> None:
    _ = importlib.import_module("app.models")  # register the complete model catalogue
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--bank-id", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--reencrypt", action="store_true")
    args = parser.parse_args(namespace=_Arguments())
    sessions = cast(Callable[[], Callable[[], Session]], database.get_sessionmaker)()
    with sessions() as db:
        result = backfill_bank(
            db,
            organization_id=args.organization_id,
            bank_id=args.bank_id,
            batch_size=args.batch_size,
            reencrypt=args.reencrypt,
        )
        print(
            f"Checked {result.checked}; encrypted {result.encrypted}; batches {result.batches}; "
            f"retained immutable {result.retained_immutable}."
        )


if __name__ == "__main__":
    main()
