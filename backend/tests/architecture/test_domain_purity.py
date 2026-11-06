"""Executable controls for the static domain import policy, not runtime purity."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture._domain_purity import STATE_CLIENTS, domain_files, domain_state_imports
from tests.architecture._planes import APP, imported_modules


def _write(app: Path, relative: str, source: str) -> Path:
    path = app / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture
def pure_app(tmp_path: Path) -> Path:
    """A small pure tree with the same two reviewed contract edges as production."""
    app = tmp_path / "app"
    for relative, source in {
        "domain/amount.py": "from decimal import Decimal\nUNIT = Decimal('1')\n",
        "capital/domain/engine.py": (
            "from app.domain.amount import UNIT\ndef capital(balance): return balance * UNIT\n"
        ),
        "data_engine/domain/ingestion/contracts.py": (
            "from app.core.key_management.concentration_identity import counterparty_group_key\n"
        ),
        "core/key_management/concentration_identity.py": (
            "def counterparty_group_key(name): return 'cp:' + name\n"
        ),
        "bi/domain/packs/__init__.py": "from app.bi.schemas.query import BiPackSpec\n",
        "bi/schemas/query.py": (
            "from pydantic import BaseModel\nclass BiPackSpec(BaseModel):\n    id: str\n"
        ),
    }.items():
        _write(app, relative, source)
    return app


def test_domain_discovery_covers_the_actual_tree() -> None:
    expected = {
        path
        for path in APP.rglob("*.py")
        if path.relative_to(APP).parts[0] == "domain"
        or path.relative_to(APP).parts[1:2] == ("domain",)
    }
    found = domain_files(APP)
    assert set(found) == expected
    assert len(found) == len(set(found))
    for owner in ("data_engine", "market_data", "liquidity"):
        assert any(path.is_relative_to(APP / owner / "domain") for path in found)


@pytest.mark.parametrize("missing", ["shared", "features", "empty"])
def test_domain_discovery_refuses_vacuous_populations(tmp_path: Path, missing: str) -> None:
    app = tmp_path / "app"
    if missing != "shared":
        _write(app, "domain/engine.py", "from decimal import Decimal\n")
    if missing != "features":
        (app / "new_feature" / "domain").mkdir(parents=True)
    with pytest.raises(AssertionError, match="domain root"):
        domain_files(app)


def test_a_new_feature_domain_is_discovered_without_registering_it(pure_app: Path) -> None:
    path = _write(
        pure_app,
        "future_product/domain/engine.py",
        "from app.capital.domain.engine import capital\n",
    )
    assert path in domain_files(pure_app)
    assert domain_state_imports(pure_app) == {}


@pytest.mark.parametrize(
    "owner,target",
    [
        ("liquidity", "app.liquidity.service.regulatory_liquidity"),
        ("market_data", "app.market_data.service.market_data"),
        ("data_engine", "app.data_engine.service.ingestion"),
        ("capital", "sqlalchemy"),
        ("capital", "app.db.session"),
    ],
)
def test_all_five_audit_escapes_are_reported(pure_app: Path, owner: str, target: str) -> None:
    if target.startswith("app."):
        _write(pure_app, target.removeprefix("app.").replace(".", "/") + ".py", "")
    _write(pure_app, f"{owner}/domain/probe.py", f"import {target}\n")
    assert domain_state_imports(pure_app) == {f"app.{owner}.domain.probe": {target}}


@pytest.mark.parametrize(
    "target",
    [
        "app.capital.api.routes",
        "app.capital.service.engine",
        "app.capital.services.engine",
        "app.capital.models",
        "app.capital.jobs",
        "app.capital.public",
        "app.capital.schemas",
        "app.api.deps",
        "app.services.audit",
        "app.models",
        "app.jobs.queue",
        "app.features.legacy",
        "app.db.session",
        "app.storage.public",
        "app.integrations.storage",
        "app.core.config",
        "app.core.key_management.fields",
        "app.bi.schemas.query.BiQuery",
    ],
)
def test_application_state_is_forbidden_independently_of_owner(pure_app: Path, target: str) -> None:
    _write(pure_app, "capital/domain/probe.py", f"import {target}\n")
    assert domain_state_imports(pure_app) == {"app.capital.domain.probe": {target}}


@pytest.mark.parametrize("client", sorted(STATE_CLIENTS))
def test_direct_state_clients_are_forbidden(pure_app: Path, client: str) -> None:
    _write(pure_app, "capital/domain/probe.py", f"from {client} import Client\n")
    assert domain_state_imports(pure_app) == {"app.capital.domain.probe": {f"{client}.Client"}}


@pytest.mark.parametrize(
    "relative",
    [
        "capital/domain/probe.py",
        "bi/schemas/query.py",
        "capital/__init__.py",
    ],
)
@pytest.mark.parametrize(
    "source,target",
    [
        ("import urllib.request\n", "urllib.request"),
        ("from urllib import request\n", "urllib.request"),
        ("from urllib.request import urlopen\n", "urllib.request.urlopen"),
        ("import http.client\n", "http.client"),
        ("from http import client\n", "http.client"),
        ("from http.client import HTTPSConnection\n", "http.client.HTTPSConnection"),
        ('import importlib\nimportlib.import_module("urllib.request")\n', "urllib.request"),
        ('__import__("http.client")\n', "http.client"),
    ],
)
def test_stdlib_http_clients_are_forbidden(
    pure_app: Path, relative: str, source: str, target: str
) -> None:
    _write(pure_app, relative, source)
    module = "app." + relative.removesuffix(".py").replace("/", ".")
    assert domain_state_imports(pure_app) == {module: {target}}


@pytest.mark.parametrize(
    "source",
    [
        "import urllib.parse\n",
        "from urllib import parse\n",
        "from urllib.parse import urlparse\n",
        "from http import HTTPStatus\n",
        "from http.cookies import SimpleCookie\n",
        'import importlib\nimportlib.import_module("urllib.parse")\n',
        '__import__("http")\n',
    ],
)
def test_pure_stdlib_url_and_http_helpers_are_allowed(pure_app: Path, source: str) -> None:
    _write(pure_app, "capital/domain/probe.py", source)
    assert domain_state_imports(pure_app) == {}


@pytest.mark.parametrize(
    "source,target",
    [
        ("from ..service import engine\n", "app.capital.service.engine"),
        ("from .. import public\n", "app.capital.public"),
        ("from ...db import session\n", "app.db.session"),
        ("def compute():\n    import sqlalchemy.orm\n", "sqlalchemy.orm"),
        ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import fastapi\n", "fastapi"),
        (
            'import importlib\nimportlib.import_module("app.capital.service.engine")\n',
            "app.capital.service.engine",
        ),
        (
            'import importlib as loader\nloader.import_module(name="app.db.session")\n',
            "app.db.session",
        ),
        (
            'from importlib import import_module as load\nload("sql" + "alchemy")\n',
            "sqlalchemy",
        ),
        (
            'import importlib\nimportlib.import_module("..service.engine", "app.capital.domain")\n',
            "app.capital.service.engine",
        ),
        (
            "import importlib\n"
            'importlib.import_module("..service.engine", package="app.capital.domain")\n',
            "app.capital.service.engine",
        ),
        ('__import__("boto3")\n', "boto3"),
        ('__import__("app", fromlist=["db"])\n', "app.db"),
        ('from builtins import __import__ as load\nload("httpx")\n', "httpx"),
    ],
)
def test_real_import_syntax_reaches_the_policy(pure_app: Path, source: str, target: str) -> None:
    _write(pure_app, "capital/domain/probe.py", source)
    assert domain_state_imports(pure_app) == {"app.capital.domain.probe": {target}}


def test_relative_imports_in_package_initializers_use_the_package(pure_app: Path) -> None:
    _write(pure_app, "capital/domain/__init__.py", "from .. import service\n")
    assert domain_state_imports(pure_app) == {
        "app.capital.domain.__init__": {"app.capital.service"}
    }


@pytest.mark.parametrize(
    "source,module,target",
    [
        ("from .engine import capital\n", "app.capital.domain.probe", "app.capital.domain.engine"),
        (
            "from .engine import capital\n",
            "app.capital.domain.__init__",
            "app.capital.domain.engine",
        ),
        (
            'import importlib\nimportlib.import_module(".engine", package="app.capital.domain")\n',
            "app.capital.domain.probe",
            "app.capital.domain.engine",
        ),
        (
            'from importlib import import_module as load\nload("app.domain.amount")\n',
            "app.capital.domain.probe",
            "app.domain.amount",
        ),
    ],
)
def test_pure_import_controls_are_resolved_not_ignored(
    pure_app: Path, source: str, module: str, target: str
) -> None:
    assert target in imported_modules(source, module=module)
    _write(pure_app, module.removeprefix("app.").replace(".", "/") + ".py", source)
    assert domain_state_imports(pure_app) == {}


def test_pure_imports_and_harmless_prose_are_not_false_positives(pure_app: Path) -> None:
    _write(
        pure_app,
        "capital/domain/probe.py",
        '"""Never import app.db.session, sqlalchemy or app.capital.service."""\n'
        "# from app.models import Bank\n"
        'FORBIDDEN = "import fastapi"\n'
        "from decimal import Decimal\n"
        "from pydantic import BaseModel\n"
        "from numpy import array\n"
        "from scipy.interpolate import PchipInterpolator\n"
        "from .engine import capital\n"
        "from ...domain.amount import UNIT\n"
        "def amount(value): return capital(Decimal(value)) * UNIT\n",
    )
    assert domain_state_imports(pure_app) == {}
    # Component boundaries, not accidental string prefixes.
    assert imported_modules("import sqlalchemy_helpers\n") == {"sqlalchemy_helpers"}
    _write(pure_app, "capital/domain/probe.py", "import sqlalchemy_helpers\n")
    assert domain_state_imports(pure_app) == {}


@pytest.mark.parametrize(
    "relative",
    [
        "core/key_management/concentration_identity.py",
        "bi/schemas/query.py",
        "core/key_management/__init__.py",
        "bi/schemas/__init__.py",
        "capital/__init__.py",
        "__init__.py",
    ],
)
def test_reviewed_contracts_and_package_parents_cannot_hide_state(
    pure_app: Path, relative: str
) -> None:
    _write(pure_app, relative, "from app.db.session import get_engine\n")
    module = "app." + relative.removesuffix(".py").replace("/", ".")
    assert domain_state_imports(pure_app) == {module: {"app.db.session.get_engine"}}


def test_reviewed_contracts_cannot_launder_state_through_another_domain(pure_app: Path) -> None:
    _write(pure_app, "bi/schemas/query.py", "from app.capital.domain.engine import capital\n")
    _write(pure_app, "capital/domain/engine.py", "import sqlalchemy\n")
    assert domain_state_imports(pure_app) == {"app.capital.domain.engine": {"sqlalchemy"}}


@pytest.mark.parametrize(
    "relative,source",
    [
        ("capital/domain/probe.py", "from app.bi.schemas.query import BiPackSpec\n"),
        ("bi/domain/packs/__init__.py", "from app.bi.schemas.query import BiQuery\n"),
        (
            "capital/domain/probe.py",
            "from app.core.key_management.concentration_identity import counterparty_group_key\n",
        ),
        (
            "data_engine/domain/ingestion/contracts.py",
            "from app.core.key_management.concentration_identity import other_helper\n",
        ),
    ],
)
def test_allowances_are_exact_importer_and_symbol_edges(
    pure_app: Path, relative: str, source: str
) -> None:
    _write(pure_app, relative, source)
    assert domain_state_imports(pure_app)


def test_nonliteral_dynamic_names_are_not_claimed_as_resolved() -> None:
    for source in (
        "importlib.import_module(target)\n",
        'importlib.import_module(f"app.{owner}.service")\n',
        'importlib.import_module(".".join(parts))\n',
    ):
        assert imported_modules(source) == set()
    assert imported_modules('catalogue.import_module("app.db.session")\n') == set()
