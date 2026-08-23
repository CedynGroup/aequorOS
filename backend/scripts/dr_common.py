"""Shared primitives for the AequorOS backup / restore / disaster-recovery tooling.

Three properties this module exists to guarantee, because each one has a silent
failure mode that a naive `pg_dump | pg_restore` pair does not catch:

1. **A backup taken through RLS is silently incomplete.** Every tenant table is
   ``FORCE ROW LEVEL SECURITY`` and the policies key off the
   ``app.organization_id`` GUC. A role without ``BYPASSRLS`` matches zero rows
   for every tenant, so ``pg_dump`` succeeds, exits 0, and writes an archive
   containing the schema and almost none of the data. There is no error and no
   warning. :func:`assert_dump_role_sees_all_rows` is the guard.

2. **An older ``pg_dump`` cannot read a newer server.** The client refuses with
   a server-version error only after the operator believes a backup is running.
   :func:`discover_toolchain` searches for the newest client on the box and
   :func:`assert_client_can_dump` fails fast with the actual numbers.

3. **A restore aimed at the primary destroys it.** Comparison is on
   host/port/database, not on the URL string, so a different username or driver
   prefix pointing at the same database is still refused
   (:func:`refuse_protected_target`).

Nothing here ever logs a credential: URLs are carried opaquely and rendered for
humans only through :func:`describe`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Tenant policies read this GUC; the restore-time isolation probe sets it.
TENANT_GUC = "app.organization_id"

_DRIVER_PREFIX = re.compile(r"^postgresql\+\w+://")
# Homebrew (macOS) and Debian/Ubuntu (server) client layouts, newest first.
_CLIENT_SEARCH_GLOBS = ("/opt/homebrew/opt/postgresql@*/bin", "/usr/lib/postgresql/*/bin")


class DisasterRecoveryError(RuntimeError):
    """A backup, restore or verification precondition was not met."""


def to_libpq(url: str) -> str:
    """Strip a SQLAlchemy driver prefix so libpq tools accept the URL."""
    return _DRIVER_PREFIX.sub("postgresql://", url.strip())


def describe(url: str) -> str:
    """Render a URL for logs as ``host:port/database`` — never any credential."""
    parts = urlsplit(to_libpq(url))
    return f"{parts.hostname or '?'}:{parts.port or 5432}/{(parts.path or '/').lstrip('/') or '?'}"


def same_database(left: str, right: str) -> bool:
    """Whether two URLs address the same database on the same host and port.

    Deliberately ignores username, password and driver prefix: a restore aimed
    at the primary through a second role is still aimed at the primary.
    """
    a, b = urlsplit(to_libpq(left)), urlsplit(to_libpq(right))
    return (
        (a.hostname or "").lower() == (b.hostname or "").lower()
        and (a.port or 5432) == (b.port or 5432)
        and (a.path or "").lstrip("/") == (b.path or "").lstrip("/")
    )


def refuse_protected_target(target_url: str, protected: Iterable[str | None]) -> None:
    """Raise if ``target_url`` addresses any protected (production) database."""
    for candidate in protected:
        if candidate and candidate.strip() and same_database(target_url, candidate):
            msg = (
                f"Refusing to operate on {describe(target_url)}: it is a protected "
                "production database. Point at a dedicated restore target."
            )
            raise DisasterRecoveryError(msg)


def same_cluster(left: str, right: str) -> bool:
    """Whether two URLs address the same PostgreSQL cluster (host and port)."""
    a, b = urlsplit(to_libpq(left)), urlsplit(to_libpq(right))
    return (a.hostname or "").lower() == (b.hostname or "").lower() and (a.port or 5432) == (
        b.port or 5432
    )


def refuse_protected_cluster(cluster_url: str, protected: Iterable[str | None]) -> None:
    """Raise if ``cluster_url`` is the cluster hosting a production database.

    Stricter than :func:`refuse_protected_target` on purpose: a drill creates
    and drops databases, so it must be kept off the primary's cluster entirely,
    not merely off the primary database.
    """
    for candidate in protected:
        if candidate and candidate.strip() and same_cluster(cluster_url, candidate):
            msg = (
                f"Refusing to run a drill on {describe(cluster_url)}: that cluster hosts a "
                "production database, and a drill creates and drops databases on it. "
                "Use a separate cluster."
            )
            raise DisasterRecoveryError(msg)


def protected_urls_from_env() -> list[str]:
    """The URLs that must never be a restore target, read from the environment."""
    names = ("DATABASE_URL", "WORKER_DATABASE_URL", "OPERATOR_DATABASE_URL")
    return [value for name in names if (value := os.getenv(name, "").strip())]


def load_env_file(path: Path, *, override: bool = False) -> list[str]:
    """Load ``backend/.env`` into the process environment; return the names loaded.

    This exists because ``backend/.env`` is written ``KEY = value`` **with spaces
    around the equals sign**. ``set -a; . ./.env`` does not parse that — the
    shell reads ``KEY`` as a command — and a ``grep '^KEY='`` misses every line.
    An operator who works around it by hand ends up pasting a database URL onto
    a command line, where it lands in shell history and ``ps``. Parsing it here
    is the safer path, so the tooling offers it rather than assuming the
    operator will get the quoting right under incident pressure.

    Existing environment values win unless ``override`` is set, so an explicit
    export always beats the file.
    """
    if not path.is_file():
        msg = f"Environment file not found: {path}"
        raise DisasterRecoveryError(msg)
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key.replace("_", "").isalnum():
            continue
        value = value.strip().strip('"').strip("'")
        if override or not os.environ.get(key):
            os.environ[key] = value
        loaded.append(key)
    return loaded


@dataclass(frozen=True)
class Toolchain:
    """A resolved set of libpq binaries and the client major version."""

    pg_dump: str
    pg_restore: str
    psql: str
    major: int

    @property
    def bin_dir(self) -> str:
        return str(Path(self.pg_dump).parent)


def _client_major(pg_dump: str) -> int | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [pg_dump, "--version"], capture_output=True, text=True, timeout=30, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.\d+", out)
    return int(match.group(1)) if match else None


def _candidate_bin_dirs(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit)]
    dirs: list[Path] = []
    if (found := shutil.which("pg_dump")) is not None:
        dirs.append(Path(found).parent)
    for pattern in _CLIENT_SEARCH_GLOBS:
        root = Path(pattern).parts[0]
        rest = str(Path(*Path(pattern).parts[1:]))
        dirs.extend(sorted(Path(root).glob(rest)))
    return dirs


def discover_toolchain(explicit_bin_dir: str | None = None) -> Toolchain:
    """Locate the newest complete libpq toolchain available on this machine.

    ``PG_BIN_DIR`` pins the choice. Otherwise ``PATH`` is considered alongside
    the standard Homebrew and Debian multi-version layouts, and the highest
    major version wins — a box with both 14 and 16 installed must use 16 to
    back up a 15 server, and defaulting to ``PATH`` alone would silently pick
    the wrong one.
    """
    explicit = explicit_bin_dir or os.getenv("PG_BIN_DIR") or None
    best: Toolchain | None = None
    for directory in _candidate_bin_dirs(explicit):
        dump, restore, psql = (directory / name for name in ("pg_dump", "pg_restore", "psql"))
        if not (dump.is_file() and restore.is_file() and psql.is_file()):
            continue
        major = _client_major(str(dump))
        if major is None:
            continue
        if best is None or major > best.major:
            best = Toolchain(str(dump), str(restore), str(psql), major)
    if best is None:
        msg = (
            "No PostgreSQL client toolchain (pg_dump/pg_restore/psql) was found. "
            "Install the client package or set PG_BIN_DIR to its bin directory."
        )
        raise DisasterRecoveryError(msg)
    return best


def assert_client_can_dump(client_major: int, server_version: str) -> None:
    """Fail fast when the local client is older than the server it must dump."""
    server_major = int(server_version.split(".", maxsplit=1)[0])
    if client_major < server_major:
        msg = (
            f"pg_dump major {client_major} cannot dump a PostgreSQL {server_version} "
            f"server. Install client {server_major} or newer (or set PG_BIN_DIR to a "
            f"{server_major}+ bin directory) and retry."
        )
        raise DisasterRecoveryError(msg)


def assert_dump_role_sees_all_rows(*, role: str, superuser: bool, bypassrls: bool) -> None:
    """Refuse to back up through a role that RLS would filter.

    This is the single most dangerous silent failure in this codebase's backup
    story: the dump would succeed and be nearly empty. See the module docstring.
    """
    if superuser or bypassrls:
        return
    msg = (
        f"Role {role!r} has neither SUPERUSER nor BYPASSRLS. Every tenant table is "
        "FORCE ROW LEVEL SECURITY, so this role reads zero rows and the resulting "
        "backup would be silently empty. Use the BYPASSRLS role (WORKER_DATABASE_URL)."
    )
    raise DisasterRecoveryError(msg)


def run_tool(
    argv: Sequence[str], *, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run a libpq binary with an explicit environment and no shell."""
    return subprocess.run(  # noqa: S603 - fixed argv from resolved paths, shell=False
        list(argv), capture_output=True, text=True, timeout=timeout, env=env, check=False
    )


def tool_env(url: str, *, read_only: bool = False) -> dict[str, str]:
    """Environment for a libpq child process, with the credential out of argv.

    The URL is decomposed into ``PG*`` variables rather than handed to the tool
    as ``-d <url>``: a connection string on the command line is visible to every
    user on the box through ``ps``, and lands in shell history. ``read_only``
    additionally pins the child's transactions read-only, so a backup cannot
    write to the source even if a tool changed behaviour.
    """
    parts = urlsplit(to_libpq(url))
    env = dict(os.environ)
    env["PGHOST"] = parts.hostname or "localhost"
    env["PGPORT"] = str(parts.port or 5432)
    env["PGDATABASE"] = (parts.path or "/").lstrip("/")
    if parts.username:
        env["PGUSER"] = parts.username
    if parts.password:
        env["PGPASSWORD"] = parts.password
    env["PGCONNECT_TIMEOUT"] = env.get("PGCONNECT_TIMEOUT", "30")
    if read_only:
        existing = env.get("PGOPTIONS", "")
        env["PGOPTIONS"] = f"{existing} -c default_transaction_read_only=on".strip()
    return env
