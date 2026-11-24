"""Programmatic Alembic: upgrade and reset the gateway's own migration chain."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from alembic.config import Config

from alembic import command

API_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: str, *, schema: str | None = None, app_role: str | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    args = [f"url={url}"]
    if schema:
        args.append(f"schema={schema}")
    if app_role:
        args.append(f"app_role={app_role}")
    config.cmd_opts = Namespace(x=args)
    return config


def upgrade(url: str, *, schema: str | None = None, app_role: str | None = None) -> None:
    command.upgrade(alembic_config(url, schema=schema, app_role=app_role), "head")


def reset(url: str, *, schema: str | None = None) -> None:
    config = alembic_config(url, schema=schema)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
