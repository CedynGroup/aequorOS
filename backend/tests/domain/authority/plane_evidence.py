"""Metric-name extractors for the three planes the D-9 gate could not see.

The completeness gate in :mod:`test_registry_completeness` reads one plane: the
``RegulatoryMetricResult`` rows sealed into a filing run. Its own docstring named
what it left out, and that list is where the next ``car_pct_end`` would hide:

* **``RegulatoryLineItem`` rows** — the second half of what a sealed run persists.
* **``live_metrics``** — what every module's ``compute_live`` upserts and the
  Treasury/ALM cockpit reads daily.
* **the SDI read-side summaries** — figures with thresholds and statuses served
  straight from ``app/services/sdi_*.py`` with no ``RegulatoryRun`` at all.

None of the three names its metric at the persistence site, which is why one
extractor could not reach them. This module supplies three that can, and holds to
the same rule as the first: **refuse rather than shrink**. Every extractor raises,
naming the file and line, when it meets a shape it cannot read. A gate that
silently narrows its own scope is worse than no gate.

What each plane means for authority is NOT decided here — that is
:meth:`MetricAuthorityRegistry.check_completeness`. This module only reads.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

_APP_ROOT = pathlib.Path(__file__).resolve().parents[3] / "app"

#: The live-plane carrier. Every module's ``compute_live`` returns one, and its
#: ``metrics`` mapping is verbatim what lands in ``live_metrics.metrics``.
_LIVE_RESULT_MODEL = "LiveModuleResult"

#: Line-item carriers. The engine dataclasses are pure and per-domain; the ORM row
#: is written once per module from ``item.section`` / ``item.line_code``, so the
#: names only exist in the engines.
_LINE_ITEM_SUFFIX = "LineItem"


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(_APP_ROOT.parent))


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings, for one hop of resolution."""
    constants: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _module_string_sequences(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Module-level ``NAME = ("a", "b")`` bindings of string literals."""
    sequences: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if not isinstance(value, ast.Tuple | ast.List) or not value.elts:
            continue
        if not all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in value.elts
        ):
            continue
        literals = tuple(str(element.value) for element in value.elts)  # type: ignore[attr-defined]
        for target in targets:
            if isinstance(target, ast.Name):
                sequences[target.id] = literals
    return sequences


# ---------------------------------------------------------------------------
# plane 1 - the live plane
# ---------------------------------------------------------------------------


def _dict_keys(
    node: ast.expr,
    *,
    sequences: dict[str, tuple[str, ...]],
    path: pathlib.Path,
) -> Iterator[str]:
    """Literal keys of a metrics mapping, or an assertion naming why not."""
    if not isinstance(node, ast.Dict):
        raise AssertionError(
            f"{_rel(path)}:{node.lineno}: the metrics mapping handed to "
            f"{_LIVE_RESULT_MODEL} is a {type(node).__name__}, not a dict literal, so "
            "the authority gate cannot see which figures this module publishes to the "
            "live cockpit. Build it as a literal, or teach this extractor the shape."
        )
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            # ``**{...}`` - readable only when it unpacks a comprehension over a
            # module-level tuple of literal field names, which is how the forecast
            # module publishes its summary fields.
            yield from _unpacked_keys(value, sequences=sequences, path=path)
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            yield key.value
            continue
        raise AssertionError(
            f"{_rel(path)}:{key.lineno}: a live metric key is not a string literal, so "
            "the figure it publishes is invisible to the authority gate."
        )


def _unpacked_keys(
    node: ast.expr,
    *,
    sequences: dict[str, tuple[str, ...]],
    path: pathlib.Path,
) -> Iterator[str]:
    if isinstance(node, ast.Dict):
        yield from _dict_keys(node, sequences=sequences, path=path)
        return
    if (
        isinstance(node, ast.DictComp)
        and isinstance(node.key, ast.Name)
        and len(node.generators) == 1
    ):
        generator = node.generators[0]
        if (
            isinstance(generator.target, ast.Name)
            and generator.target.id == node.key.id
            and isinstance(generator.iter, ast.Name)
            and generator.iter.id in sequences
        ):
            yield from sequences[generator.iter.id]
            return
    raise AssertionError(
        f"{_rel(path)}:{node.lineno}: a live metrics mapping unpacks an expression this "
        "extractor cannot resolve to literal field names, so those figures would leave "
        "the authority gate's scope silently."
    )


def _live_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == _LIVE_RESULT_MODEL)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == _LIVE_RESULT_MODEL)
        )
    ]


def _local_dict_bindings(tree: ast.Module) -> dict[str, ast.Dict]:
    """``name = {...}`` anywhere in the module, for the ``metrics=name`` shape."""
    bindings: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            bindings[node.target.id] = node.value
    return bindings


def live_metric_codes() -> dict[str, str]:
    """``live metric key -> file:line`` for every figure written to ``live_metrics``."""
    found: dict[str, str] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if _LIVE_RESULT_MODEL not in source:
            continue
        tree = ast.parse(source)
        calls = _live_calls(tree)
        if not calls:
            continue
        sequences = _module_string_sequences(tree)
        bindings = _local_dict_bindings(tree)
        for call in calls:
            keyword = next((kw for kw in call.keywords if kw.arg == "metrics"), None)
            assert keyword is not None, (
                f"{_rel(path)}:{call.lineno}: {_LIVE_RESULT_MODEL} is constructed without "
                "an explicit metrics=, so the gate cannot tell which figures this module "
                "publishes to the live plane."
            )
            value = keyword.value
            if isinstance(value, ast.Name):
                resolved = bindings.get(value.id)
                assert resolved is not None, (
                    f"{_rel(path)}:{call.lineno}: metrics={value.id!r} is not bound to a "
                    "dict literal this extractor can read."
                )
                value = resolved
            for code in _dict_keys(value, sequences=sequences, path=path):
                found.setdefault(code, f"{_rel(path)}:{value.lineno}")
    return found


# ---------------------------------------------------------------------------
# plane 2 - the filed line-item plane
# ---------------------------------------------------------------------------


def _line_item_classes(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith(_LINE_ITEM_SUFFIX)
    }


def all_line_item_classes() -> frozenset[str]:
    classes: set[str] = set()
    for path in sorted(_APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if _LINE_ITEM_SUFFIX not in source:
            continue
        classes |= _line_item_classes(ast.parse(source))
    return frozenset(classes)


def _helper_literal_arguments(tree: ast.Module) -> dict[tuple[str, str], set[str]]:
    """``(function, parameter) -> the string literals every call site passes it``.

    ``_ratio_line_item("car", ...)`` is where ``car``, ``cet1_ratio``,
    ``tier1_ratio`` and ``leverage_ratio`` are actually named — the constructor
    inside the helper only ever sees a parameter. One hop of resolution reaches
    them; more than one hop would be guessing, and this module does not guess.
    """
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    arguments: dict[tuple[str, str], set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        target = functions.get(node.func.id)
        if target is None:
            continue
        names = [argument.arg for argument in target.args.args]
        for index, value in enumerate(node.args):
            if (
                index < len(names)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                arguments.setdefault((node.func.id, names[index]), set()).add(value.value)
        for keyword in node.keywords:
            if (
                keyword.arg
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                arguments.setdefault((node.func.id, keyword.arg), set()).add(keyword.value.value)
    return arguments


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str | None:
    for candidate in ast.walk(tree):
        if not isinstance(candidate, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(candidate):
            if child is node:
                return candidate.name
    return None


def _resolve_line_code(
    value: ast.expr,
    *,
    tree: ast.Module,
    node: ast.Call,
    constants: dict[str, str],
    helpers: dict[tuple[str, str], set[str]],
) -> tuple[str, ...] | None:
    """The literal names a ``line_code=`` can carry, or ``None`` when data-keyed."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return (value.value,)
    if isinstance(value, ast.Name):
        if value.id in constants:
            return (constants[value.id],)
        enclosing = _enclosing_function(tree, node)
        if enclosing is not None:
            resolved = helpers.get((enclosing, value.id))
            if resolved:
                return tuple(sorted(resolved))
    return None


def line_item_codes() -> tuple[dict[str, str], dict[str, str]]:
    """``(named, data_keyed)`` line codes, each ``code -> file:line``.

    **Named** codes are literals the platform chose: ``car``, ``portfolio_var``,
    ``diversification_benefit``. They are a fixed vocabulary and can carry an
    authority. **Data-keyed** codes are the bank's own book — a GL category, a
    currency, a product, a tenor label — reported under the expression that
    produces them. A registry cannot enumerate those and should not pretend to;
    they are recorded so the boundary is measured rather than assumed.
    """
    classes = all_line_item_classes()
    named: dict[str, str] = {}
    data_keyed: dict[str, str] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(f"{name}(" in source for name in classes):
            continue
        tree = ast.parse(source)
        constants = _module_string_constants(tree)
        helpers = _helper_literal_arguments(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in classes
            ):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            section = keywords.get("section")
            code = keywords.get("line_code")
            if section is None and node.args:
                section = node.args[0]
            if code is None and len(node.args) > 1:
                code = node.args[1]
            assert code is not None, (
                f"{_rel(path)}:{node.lineno}: {node.func.id} is constructed with no "
                "line_code, so the figure it files cannot be named."
            )
            section_name = (
                section.value
                if isinstance(section, ast.Constant) and isinstance(section.value, str)
                else _resolve_section(section, tree=tree, node=node, constants=constants)
            )
            resolved = _resolve_line_code(
                code, tree=tree, node=node, constants=constants, helpers=helpers
            )
            site = f"{_rel(path)}:{node.lineno}"
            if resolved is None:
                data_keyed.setdefault(f"{section_name or '?'}:{ast.unparse(code)}", site)
                continue
            for literal in resolved:
                named.setdefault(f"{section_name or '?'}:{literal}", site)
    return named, data_keyed


def _resolve_section(
    value: ast.expr | None,
    *,
    tree: ast.Module,
    node: ast.Call,
    constants: dict[str, str],
) -> str | None:
    if value is None:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name) and value.id in constants:
        return constants[value.id]
    return None


# ---------------------------------------------------------------------------
# plane 3 - the SDI read-side summaries
# ---------------------------------------------------------------------------

#: Read-side carriers that publish a figure WITH a threshold and a status — the
#: shape that makes a number a supervisory judgment rather than a display value.
_JUDGMENT_FIELDS = frozenset({"threshold_pct", "threshold", "status"})


def sdi_read_side_codes() -> dict[str, str]:
    """``code -> file:line`` for SDI read-side figures carrying a threshold.

    These never touch ``RegulatoryRun``: ``app/services/sdi_*.py`` computes them
    per request and the s.29 screens render them. A figure served with a
    threshold and a pass/fail status is a judgment about the institution however
    it is transported, so the gate must be able to see it.
    """
    found: dict[str, str] = {}
    for path in sorted(_APP_ROOT.glob("services/sdi_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_string_constants(tree)
        helpers = _helper_literal_arguments(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            if "code" not in keywords or not (_JUDGMENT_FIELDS & set(keywords)):
                continue
            resolved = _resolve_line_code(
                keywords["code"], tree=tree, node=node, constants=constants, helpers=helpers
            )
            if resolved is None:
                continue
            for literal in resolved:
                found.setdefault(literal, f"{_rel(path)}:{node.lineno}")
    return found
