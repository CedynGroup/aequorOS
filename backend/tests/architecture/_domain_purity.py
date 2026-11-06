"""Static domain-state boundary, independent of feature ownership and layering.

Scan every shared/feature domain file, including package-local registration
modules. The two reviewed non-domain dependencies and their package initializers
are scanned too, so an allowance cannot acquire a stateful import unnoticed.
Bundled BI pack JSON loading is intentionally unchanged: purity here means no
application state, not no import-time filesystem access.

This is an import-policy check, not a runtime side-effect proof. It inherits the
shared scanner's limits for nonliteral names, reflection and alias rebinding;
third-party dependency internals are not traversed.
"""

from __future__ import annotations

from pathlib import Path

from tests.architecture._planes import imported_modules

# Exact importer -> imported symbol, not an exemption for core or schemas.
PURE_CONTRACT_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "app.data_engine.domain.ingestion.contracts",
            "app.core.key_management.concentration_identity.counterparty_group_key",
        ),
        ("app.bi.domain.packs", "app.bi.schemas.query.BiPackSpec"),
    }
)

# Direct database, HTTP and object-storage clients. Numerical libraries and
# Pydantic wire contracts are not state clients. This is a bounded policy, not
# a claim to recognize every possible third-party I/O library.
STATE_CLIENTS: frozenset[str] = frozenset(
    {
        "sqlalchemy",
        "alembic",
        "fastapi",
        "starlette",
        "boto3",
        "botocore",
        "minio",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "sqlite3",
        "oracledb",
        "pyodbc",
        "jaydebeapi",
        "snowflake.connector",
        "redis",
        "requests",
        "httpx",
        "aiohttp",
        "urllib.request",
        "http.client",
    }
)


def domain_files(app: Path) -> list[Path]:
    """Discover actual domain roots; missing/empty roots must not pass silently."""
    shared = app / "domain"
    assert shared.is_dir(), f"Missing shared domain root: {shared}"
    feature_roots = sorted(path for path in app.glob("*/domain") if path.is_dir())
    assert feature_roots, f"No feature domain roots found under {app}"
    found: list[Path] = []
    for root in [shared, *feature_roots]:
        files = sorted(root.rglob("*.py"))
        assert files, f"Empty domain root: {root}"
        found.extend(files)
    return sorted(found)


def _module(path: Path, app: Path) -> str:
    return ".".join(("app", *path.relative_to(app).with_suffix("").parts))


def _leaf_imports(names: set[str]) -> set[str]:
    # The scanner emits both x and x.y for "from x import y".
    return {name for name in names if not any(other.startswith(f"{name}.") for other in names)}


def _parents(path: Path, app: Path) -> set[Path]:
    return {
        parent / "__init__.py"
        for parent in path.parents
        if (parent == app or app in parent.parents) and (parent / "__init__.py").is_file()
    }


def domain_state_imports(app: Path) -> dict[str, set[str]]:
    """Report forbidden imports, including those in reviewed contracts/parents.

    Application imports are closed by default: only actual domain modules and
    the exact reviewed symbol edges above are admitted. Thus same-feature
    services, public barrels, schemas, kernel state and future application
    namespaces cannot bypass the rule by feature ownership.
    """
    domains = set(domain_files(app))
    modules = {_module(path, app).removesuffix(".__init__"): path for path in app.rglob("*.py")}
    contracts: set[Path] = set()
    for _, target in PURE_CONTRACT_IMPORTS:
        module = target.rpartition(".")[0]
        assert module in modules, f"Reviewed pure contract is missing: {module}"
        contracts.add(modules[module])
    checked = domains | contracts
    checked |= {parent for path in checked for parent in _parents(path, app)}
    offending: dict[str, set[str]] = {}
    for path in sorted(checked):
        source = _module(path, app)
        importer = source.removesuffix(".__init__")
        for target in _leaf_imports(
            imported_modules(path.read_text(encoding="utf-8"), module=source)
        ):
            if any(target == client or target.startswith(f"{client}.") for client in STATE_CLIENTS):
                offending.setdefault(source, set()).add(target)
            elif target == "app" or target.startswith("app."):
                module = target
                while module and module not in modules:
                    module = module.rpartition(".")[0]
                if (
                    modules.get(module) not in domains
                    and (importer, target) not in PURE_CONTRACT_IMPORTS
                ):
                    offending.setdefault(source, set()).add(target)
    return offending
