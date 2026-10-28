"""Control a synthetic local key; the only database accepted is ./demo.db in its directory."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.demo import (
    FileDemoKeyProvider,
    change_status,
    initialize,
    read_state,
    require_local_demo,
)
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.models import Bank


class _Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["init", "connect", "revoke", "outage", "restore", "status"]
    directory: Path
    organization: str | None = None
    bank: str | None = None


def connect_demo(directory: Path) -> None:
    require_local_demo()
    state = read_state(directory / "demo-key.json")
    database = directory.resolve() / "demo.db"
    if database.is_symlink():
        raise ValueError("The disposable demo database must not be a symlink.")
    if not database.is_file():
        raise ValueError("Bootstrap the disposable demo.db before connecting its synthetic key.")
    provider = FileDemoKeyProvider(directory / "demo-key.json")
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    try:
        with Session(engine) as db:
            bank = db.scalar(
                select(Bank).where(
                    Bank.id == state.bank_id,
                    Bank.organization_id == state.organization_id,
                )
            )
            if bank is None:
                raise ValueError("The synthetic demo bank must be provisioned first.")
            if bank.storage_slug is None:
                bank.storage_slug = bank.id.lower()
            existing = registry.bank_key(db, state.organization_id, state.bank_id)
            if existing is not None:
                if registry.reference(existing) != state.reference:
                    raise ValueError("Demo control cannot replace a connected key.")
                registry.prepare_envelope(
                    db,
                    existing,
                    {"purpose": "synthetic-demo-control"},
                    lambda _name: provider,
                )
                return
            registry.register(
                db,
                bank_id=bank.id,
                organization_id=bank.organization_id,
                storage_slug=bank.storage_slug,
                key=state.reference,
                providers=lambda _name: provider,
            )
            db.commit()
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="aequorOS synthetic local key control")
    parser.add_argument(
        "action", choices=["init", "connect", "revoke", "outage", "restore", "status"]
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--organization")
    parser.add_argument("--bank")
    options = _Options.model_validate(vars(parser.parse_args(argv)))
    try:
        require_local_demo()
        directory = options.directory.resolve()
        path = directory / "demo-key.json"
        if options.action == "init":
            if options.organization is None or options.bank is None:
                raise ValueError("init requires --organization and --bank.")
            directory.mkdir(parents=True, exist_ok=True)
            state = initialize(path, organization_id=options.organization, bank_id=options.bank)
        elif options.action == "connect":
            connect_demo(directory)
            state = read_state(path)
        elif options.action == "status":
            state = read_state(path)
        else:
            status = {
                "revoke": KeyStatus.DISABLED,
                "outage": KeyStatus.UNAVAILABLE,
                "restore": KeyStatus.ACTIVE,
            }[options.action]
            state = change_status(path, status)
        print(f"aequorOS synthetic key: {state.bank_id} {state.status.value}")
        return 0
    except (KeyUnavailableError, OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
