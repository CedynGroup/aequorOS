"""Structural guards on the one part of the platform that talks to the outside.

Four properties that cannot be left to code review, because a violation looks
harmless at the diff level and is expensive in production:

1. **Per vendor**, the transport is imported in exactly ONE module inside
   ``app/services/ai``, and only inside a function, so no ordinary process loads
   an SDK, an HTTP client or a credential. D-053 made this a per-vendor rule
   rather than a rule about ``anthropic``: the tier has three adapters now, and a
   module-level import in any one of them puts that vendor's transport in the API
   process.
2. No request may carry a parameter Opus 5 rejects with a 400.
3. Every AI tunable lives in ``core/config.py``: no numeric literal, model id or
   effort level may be written into the AI logic (D-024).
4. Failover is decided on a closed vocabulary of failure codes, so no adapter can
   smuggle in a decision based on a vendor's message text.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"

#: The one module allowed to import the Anthropic SDK. Also the module whose
#: request shape the Opus-5 parameter scan below reads.
_SDK_MODULE = _APP / "services" / "ai" / "client.py"

_AI_PACKAGE = _APP / "services" / "ai"

#: vendor transport -> the ONE module inside ``app/services/ai`` allowed to import
#: it. ``httpx`` is imported at module scope in several other services (the ORASS
#: channel, the market desk, OpenBao), so the rule is scoped to the AI package:
#: inside it, each vendor's transport belongs to that vendor's adapter alone.
_VENDOR_TRANSPORTS: dict[str, Path] = {
    "anthropic": _AI_PACKAGE / "client.py",
    "httpx": _AI_PACKAGE / "openai_model.py",
}

#: The Gemini adapter shares ``httpx`` with the OpenAI one, so its rule is the
#: lazy-import half only, checked per module below.
_HTTP_ADAPTERS = (_AI_PACKAGE / "openai_model.py", _AI_PACKAGE / "google_model.py")

#: Files the D-024 numeric scan covers. Every one of them is AI logic; the
#: settings module is deliberately NOT here, because that is where the numbers
#: are supposed to live.
_TUNABLE_SCAN = (
    *sorted((_APP / "domain" / "ai").glob("*.py")),
    *sorted((_APP / "services" / "ai").glob("*.py")),
    _APP / "domain" / "icaap" / "ai_facts.py",
    _APP / "domain" / "icaap" / "ai_convert.py",
    _APP / "services" / "icaap" / "ai_fact_sheet.py",
    _APP / "services" / "icaap" / "ai_prompt.py",
    _APP / "services" / "icaap" / "ai_drafting.py",
    _APP / "services" / "icaap" / "ai_jobs.py",
)

#: The route modules are deliberately NOT scanned: their only numbers are HTTP
#: status codes, which are protocol rather than policy. Their AI behaviour comes
#: from the dependencies and services above, which ARE scanned.

#: Numbers that are structure or protocol, never a threshold anyone would tune:
#: indexes and counts of one or two, an HTTP status, milliseconds per second,
#: and an LRU cache size.
_STRUCTURAL_NUMBERS = frozenset({0, 1, 2, 4, 529, 1000})


def _python_files() -> list[Path]:
    return [path for path in _APP.rglob("*.py") if "__pycache__" not in path.parts]


def _imports(path: Path, module: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(_names_module(node, module) for node in ast.walk(tree))


def _names_module(node: ast.AST, module: str) -> bool:
    if isinstance(node, ast.Import):
        return any(
            alias.name == module or alias.name.startswith(f"{module}.") for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        return (node.module or "") == module or (node.module or "").startswith(f"{module}.")
    return False


def test_the_sdk_is_imported_in_exactly_one_module() -> None:
    importers = [
        str(path.relative_to(_BACKEND)) for path in _python_files() if _imports(path, "anthropic")
    ]
    assert sorted(set(importers)) == [str(_SDK_MODULE.relative_to(_BACKEND))]


def test_each_vendor_transport_has_exactly_one_importer_in_the_ai_package() -> None:
    """D-053's extension of the single-importer rule to the other two vendors.

    ``httpx`` is the transport for both HTTP adapters; the OpenAI one owns the
    shared name and the Gemini one is asserted beside it, so a third module in
    this package that reached for a vendor's transport would fail here.
    """
    for module, owner in _VENDOR_TRANSPORTS.items():
        importers = sorted(
            path.name
            for path in sorted(_AI_PACKAGE.glob("*.py"))
            if _imports(path, module) and path not in (owner, *_HTTP_ADAPTERS)
        )
        assert not importers, f"{module} is imported in {importers} as well as {owner.name}"
    for adapter in _HTTP_ADAPTERS:
        assert _imports(adapter, "httpx"), f"{adapter.name} must own its own transport import"


@pytest.mark.parametrize(
    ("path", "module"),
    [
        (_SDK_MODULE, "anthropic"),
        (_AI_PACKAGE / "openai_model.py", "httpx"),
        (_AI_PACKAGE / "google_model.py", "httpx"),
    ],
    ids=lambda value: getattr(value, "name", value),
)
def test_every_vendor_transport_import_is_lazy(path: Path, module: str) -> None:
    """A module-level import would load the transport into every process that
    imports the adapter — including, transitively, the API and the core worker.

    ``if TYPE_CHECKING`` blocks are exempt: they are annotations only and are
    never executed. Everything else at module scope is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.If):
            continue
        assert not _names_module(node, module), (
            f"{module} must be imported inside a function in {path.name}, never at module scope"
        )


def test_the_model_request_passes_no_parameter_opus_5_rejects() -> None:
    """temperature/top_p/top_k and budget_tokens are all 400s on this model.

    An AST scan, not a text scan: the module docstring names them precisely
    because they must never be sent, and a text scan would convict the warning.
    """
    # ``inference_geo`` is deliberately absent: it must never be REQUESTED (there
    # is no African region to ask for), but the served geography is RECORDED from
    # the response, which is evidence a supervisor may ask for.
    banned = {"temperature", "top_p", "top_k", "budget_tokens"}
    tree = ast.parse(_SDK_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in banned:
            pytest.fail(f"{_SDK_MODULE.name}:{node.lineno} passes {node.arg}")
        if isinstance(node, ast.Constant) and node.value in banned:
            pytest.fail(f"{_SDK_MODULE.name}:{node.lineno} names {node.value} as a key")
    generate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "generate"
    )
    for node in ast.walk(generate):
        if isinstance(node, ast.Constant) and node.value == "inference_geo":
            pytest.fail("the request must never ask for an inference geography")


@pytest.mark.parametrize("path", _TUNABLE_SCAN, ids=lambda path: path.name)
def test_ai_logic_holds_no_numeric_tunable(path: Path) -> None:
    """D-024: every AI number is a setting, so it can be tuned by an operator.

    Structural numbers (0, 1, an index) are fine; anything else in this code
    would be a threshold, a limit or a window that nobody could change without
    a deployment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            if isinstance(node.value, bool) or node.value in _STRUCTURAL_NUMBERS:
                continue
            offenders.append(f"{path.name}:{node.lineno} -> {node.value!r}")
    assert not offenders, f"move these to AiSettings: {offenders}"


@pytest.mark.parametrize("path", _TUNABLE_SCAN, ids=lambda path: path.name)
def test_ai_logic_names_no_model_or_effort_level(path: Path) -> None:
    """The model id and the effort level are settings, and the row records them."""
    source = path.read_text(encoding="utf-8")
    # A model id in a DOCSTRING documents the verified API shape; one in an
    # ordinary string literal is a model the operator could not change.
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            assert "claude-" not in node.value, f"{path.name} hard-codes a model id"
            for level in ("xhigh", "medium"):
                assert node.value != level, f"{path.name} hard-codes an effort level"


def test_the_model_id_default_lives_only_in_settings() -> None:
    config = (_APP / "core" / "config.py").read_text(encoding="utf-8")
    assert 'default="claude-opus-5"' in config


def test_the_pure_ai_domain_touches_no_infrastructure() -> None:
    """``app/domain/ai`` must stay importable without a database or a network."""
    forbidden = ("sqlalchemy", "fastapi", "httpx", "requests", "anthropic", "app.services")
    for path in sorted((_APP / "domain" / "ai").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert f"import {token}" not in source, f"{path.name} imports {token}"
            assert f"from {token}" not in source, f"{path.name} imports from {token}"


def test_failover_is_decided_on_codes_not_on_vendor_messages() -> None:
    """D-053: an ungrounded draft must never advance the tier, and the decision
    must be structural.

    The three sets are disjoint and closed, and ``advances`` reads nothing else —
    so a vendor cannot talk the platform into a second attempt, and a content
    outcome cannot be mistaken for an outage.
    """
    from app.services.ai import vendors  # noqa: PLC0415 - an architecture assertion

    assert not vendors.AVAILABILITY_FAILURE_CODES & vendors.CONFIGURATION_FAILURE_CODES
    assert not vendors.CONTENT_OUTCOMES & vendors.AVAILABILITY_FAILURE_CODES
    # A 400 is our own malformed request: it would be malformed everywhere.
    assert "bad_request" not in vendors.AVAILABILITY_FAILURE_CODES
    assert "bad_request" not in vendors.CONFIGURATION_FAILURE_CODES
    source = (_AI_PACKAGE / "tiered.py").read_text(encoding="utf-8")
    for leak in ("error.message", ".text", "detail"):
        assert leak not in source, f"the tier must not branch on {leak}"


def test_every_tier_vendor_has_an_adapter_module() -> None:
    """``AI_PROVIDER_TIER`` validates against ``AI_VENDORS``; this pins that every
    accepted name actually resolves to code, so a permitted value can never be a
    silent no-op."""
    from app.core.config import AI_VENDORS  # noqa: PLC0415 - an architecture assertion
    from app.services.ai.tiered import ADAPTER_MODULES  # noqa: PLC0415

    assert sorted(ADAPTER_MODULES) == sorted(AI_VENDORS)
    for module in ADAPTER_MODULES.values():
        relative = Path(module.replace(".", "/") + ".py")
        assert (_BACKEND / relative).exists(), f"{module} does not exist"


def test_the_approved_configurations_file_ships_empty() -> None:
    """Adding an entry is a reviewed commit, and this is the tripwire for it."""
    import json  # noqa: PLC0415

    raw = json.loads(
        (_APP / "services" / "ai" / "approved_configurations.json").read_text(encoding="utf-8")
    )
    assert raw["configurations"] == [], (
        "An approved configuration has been committed. That is the LAST step of "
        "the production gate: check the data-protection opinion, the provider "
        "terms, the tenant consent text and the eval sign-off are all in place, "
        "then update this test deliberately."
    )
