"""X-5: the BI plane is downstream of everything and upstream of nothing.

BI reads the canonical book, the live plane and the sealed runs, and projects
them into the ``bi_*`` marts. Nothing in the regulatory or live plane may
depend on it, and it may never write back. That is a one-way arrow, and a
one-way arrow is exactly the kind of invariant that survives review and then
dies to a convenience import six months later — so it is pinned here, by AST,
at the moment the import is written rather than when a filed number turns out
to have come from a mart.

Five rules, each from **D-041** (and **D-043**, which moved the scheduler's
"is this bank due" read behind the seam so this allow-list needed no
amendment):

a. **The regulatory/live plane never imports BI.** The whole of ``app/`` is
   scanned except the BI-owned paths. One allow-list: ``app.services.bi.enqueue``
   and ``app.services.bi.versions`` — the enqueue seam the spec's hook table
   requires — may be imported by the five hook sites and nowhere else.
b. **BI never calls the official derivation.** ``derive_facts`` REFUSES a book
   that does not reconcile (see ``test_derivation_plane_boundary.py``); a mart
   is a read projection and must never hold that decision. Both halves are
   checked: no reference from BI, and no BI module smuggled into the
   derivation's own allow-list.
c. **BI writes ``bi_*`` tables, plus exactly the platform seams named in
   :data:`PERMITTED_DIRECT_WRITES` and :data:`PERMITTED_MEDIATED_WRITES`, and
   nothing else.** Every ``add`` / ``add_all`` / ``merge`` / bulk write and every
   ``insert()`` / ``update()`` / ``delete()`` target in EVERY BI-owned module —
   the service and domain trees, the feature routes, the jobs, the operator
   surface, the models — is resolved to a mapped class and checked against the
   ``bi_``-prefixed tables of ``Base.metadata`` — derived, not listed, so a new
   mart is covered the day its model lands. A write whose target cannot be read
   off the code is itself a violation: an unreadable write site is how the next
   one hides. A write MEDIATED by a helper imported from outside BI
   (``audit.record_event`` writing ``audit_events``, ``job_queue.enqueue``
   writing ``jobs``) is resolved through the helper's own body and convicted the
   same way unless the ``(helper, table)`` pair is allow-listed with its reason.
   Until audit A360-1 the scan covered ``app/services/bi`` and ``app/domain/bi``
   only — 68 of the 90 exempt modules — and the one direct non-``bi_*`` write in
   the tree (``ai_commentary_drafts``, from the AI job) had been placed in the
   unscanned part precisely because the guard would have convicted it under
   ``app/services/bi``; a ``db.add(CanonicalPosition(...))`` in any feature
   route would have passed every guard.
d. **``app/domain/bi/**`` imports no application state.** The BI-specific
   restatement of the pure-domain rule (``test_dependency_boundaries.py``), so
   the catalogue and the row extractors stay reusable and golden-testable.
e. **The seam is thin.** ``enqueue.py`` and ``versions.py`` import no builder,
   catalogue, compiler, executor or handler. That thinness is
   the entire justification for rule (a)'s allow-list: the hook sites run in
   the request path and the core worker, and must not pull the mart machinery
   into it. Reading the two BI MODELS is permitted and deliberate (D-043) —
   every consumer of ``app.models`` already loads ``app.models.bi`` through that
   package's ``__init__``.

Scanning limits, stated rather than implied. Imports are resolved the way
``_planes.imported_modules`` resolves them (absolute, relative, and
``importlib.import_module`` with a literal name), and a meta-test asserts this
module's line-numbered collector sees everything that shared scanner sees, so
the two cannot drift. A module name assembled at runtime is invisible to any
AST guard. Rule (c) is flow-insensitive except for one deliberate concession —
a local name resolves to its NEAREST PRECEDING assignment in the same function,
which is what ``x = cache.get(k)`` / ``if x is None: x = Model(...)`` /
``db.add(x)`` requires — and it does not see an UPDATE produced by mutating an
attribute of a row loaded from another plane. Mediated writes are followed ONE
hop out of BI: the imported helper's body is scanned, and helpers it calls in
its OWN module are followed two levels down; a helper that delegates to a third
module's writer is not followed, and a helper reached through anything but a
plain import alias (a callback, a registry lookup, a method on an object) is
invisible. Rule (c) therefore proves what is written, not everything that could
conceivably be flushed; the Postgres RLS policies and the read-only BI session
are the other half.

Every assertion names the offending ``path:line``, and every checker has a
self-proving case: a guard that has never fired proves only that it is present.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from app.db.base import Base
from tests.architecture._planes import APP, imported_modules, module_name
from tests.architecture.test_derivation_plane_boundary import (
    OFFICIAL_DERIVATION,
    OFFICIAL_DERIVATION_CALLERS,
)

BACKEND = APP.parent

# ---------------------------------------------------------------------------
# What the BI plane IS, and what may reach into it
# ---------------------------------------------------------------------------

#: Where BI lives on disk, relative to ``app/``. Every path matching one of
#: these globs is BI and is exempt from rule (a) — it is allowed to import BI
#: because it IS BI. This is D-041's list verbatim.
BI_OWNED_GLOBS: tuple[str, ...] = (
    "services/bi/**/*.py",
    "services/bi/*.py",
    "domain/bi/**/*.py",
    "domain/bi/*.py",
    # A6-09: a SUBSTRING glob ("*bi*") would silently exempt any future
    # feature whose name merely contains "bi" — ``manage_bindings.py`` is the
    # obvious one. Named prefixes/suffixes only, and the resolved set is
    # asserted below rather than observed.
    "features/read_bi.py",
    # Phase 4's machine feed is `read_bi_feeds.py`, which matches none of the
    # three patterns below: it neither starts with the token, ends with it, nor
    # equals `read_bi.py`. Named as its own prefix rather than loosened to
    # `features/*bi*.py`, for the A6-09 reason recorded above.
    "features/read_bi_*.py",
    "features/bi_*.py",
    "features/*_bi.py",
    # Phase 3 added `manage_bi_content.py`, which matches none of the three above:
    # it neither starts nor ends with the token. Named as its own prefix rather
    # than loosened to `features/*bi*.py`, for the A6-09 reason recorded above.
    "features/manage_bi_*.py",
    "jobs/bi_*.py",
    "operator/**/bi_*.py",
    # The BI plane is modelled across several modules now: the marts in `bi.py`,
    # saved content in `bi_content.py`, alerts and subscriptions in
    # `bi_notifications.py`. `models/bi.py` alone would leave the newer ones
    # outside the exemption and convict every BI module that reads them.
    "models/bi.py",
    "models/bi_*.py",
)

#: The two BI roots rules (b)–(d) scan.
BI_SERVICE_ROOT = APP / "services" / "bi"
BI_DOMAIN_ROOT = APP / "domain" / "bi"

#: Import prefixes that mean "this is the BI plane".
BI_MODULE_PREFIXES: tuple[str, ...] = ("app.services.bi", "app.domain.bi", "app.models.bi")

#: The ONLY BI modules the regulatory/live plane may import (D-041). Both are
#: deliberately thin: see rule (e).
SEAM_MODULES: frozenset[str] = frozenset({"app.services.bi.enqueue", "app.services.bi.versions"})

#: The ONLY modules outside BI that may import the seam — the spec's hook table:
#: an accepted ingestion batch, an approved or reversed withdrawal, a completed
#: live or official pipeline run, every register/entitlement/parameter trigger
#: that reflows the live plane, and the scheduler's recovery sweep.
#:
#: A new entry here is a claim that a product mutation changes what a mart would
#: show. It is not a place to put a reader: a surface that needs mart DATA calls
#: a BI feature route, which is BI and therefore not scanned at all.
SEAM_IMPORTERS: frozenset[str] = frozenset(
    {
        "app/services/ingestion.py",
        "app/services/canonical_withdrawal.py",
        "app/services/pipeline.py",
        "app/services/live_refresh_triggers.py",
        "app/services/scheduler.py",
    }
)

#: ``app/models/__init__.py`` is the SQLAlchemy metadata aggregator, not a
#: plane: it must import every model module or ``Base.metadata`` is incomplete
#: and ``create_all``, the migration RLS census and the tenant-RLS census all go
#: quietly blind. Exempted by name, and pinned narrow by
#: ``test_the_model_registry_exemption_stays_a_registry_exemption``: it may name
#: ``app.models.bi`` and must never name a BI service or domain module.
MODEL_REGISTRY = "app/models/__init__.py"

# ``app/schemas/bi.py`` is deliberately NOT exempt. It is the BI wire contract
# and needs no BI import today (it is pure pydantic), so leaving it in the scan
# means a future need to reach for the catalogue is a recorded decision rather
# than a silent widening of the boundary.


def _app_modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _bi_owned() -> frozenset[str]:
    """Every ``app/``-relative path that IS the BI plane, derived from the globs."""
    owned: set[str] = set()
    for pattern in BI_OWNED_GLOBS:
        owned.update(
            path.relative_to(BACKEND).as_posix()
            for path in APP.glob(pattern)
            if "__pycache__" not in path.parts
        )
    return frozenset(owned)


BI_OWNED: frozenset[str] = _bi_owned()

#: The feature-plane files the globs above are allowed to resolve to, named
#: rather than observed (audit A6-09). Rule (a) exempts everything in
#: :data:`BI_OWNED` from the "no module outside BI imports BI" check, so a
#: non-BI module drifting into this set is a silent boundary hole. Adding a BI
#: feature file means adding it here in the same change.
EXPECTED_BI_FEATURE_FILES: frozenset[str] = frozenset(
    {
        "app/features/read_bi.py",
        "app/features/read_bi_feeds.py",
        "app/features/ask_bi.py",
        "app/features/export_bi.py",
        "app/features/manage_bi_commentary.py",
        "app/features/manage_bi_content.py",
        "app/features/manage_bi_notifications.py",
    }
)


def test_the_bi_owned_globs_resolve_to_no_non_bi_module() -> None:
    """The exemption list is asserted, not whatever the globs happened to match.

    ``BI_OWNED`` is what rule (a) exempts. A substring glob once stood here, so
    any feature file containing "bi" would have been exempted without a test
    failing. Both directions are asserted so a renamed or deleted BI feature is
    just as loud as an unexpected new member.
    """

    features = frozenset(path for path in BI_OWNED if path.startswith("app/features/"))
    assert features == EXPECTED_BI_FEATURE_FILES, (
        "the BI-owned globs resolve to a feature file that is not named in "
        "EXPECTED_BI_FEATURE_FILES (or no longer resolve to one that is); "
        f"difference: {sorted(features ^ EXPECTED_BI_FEATURE_FILES)}"
    )
    # Nothing outside the four BI roots may be exempt at all.
    allowed_roots = (
        "app/services/bi/",
        "app/domain/bi/",
        "app/features/",
        "app/jobs/bi_",
        "app/operator/",
        "app/models/bi.py",
        # `app/models/bi_` and NOT `app/models/bi`: the shorter prefix would also
        # admit `app/models/bindings.py`, which is the exact substring hazard this
        # file records being bitten by (A6-09). The underscore is load-bearing.
        "app/models/bi_",
    )
    strays = sorted(p for p in BI_OWNED if not p.startswith(allowed_roots))
    assert not strays, f"BI_OWNED exempts a path outside the BI roots: {strays}"


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


def _bi_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _bi_tree() -> list[Path]:
    """The two roots rule (d)'s purity check and the seam rules reason about."""
    paths = _bi_files(BI_SERVICE_ROOT) + _bi_files(BI_DOMAIN_ROOT)
    assert paths, "no BI modules found — the scan would pass for the wrong reason"
    return paths


def _bi_owned_tree() -> list[Path]:
    """EVERY module rule (a) exempts — which is therefore every module rules (b)
    and (c) must scan. Exempting a file from the import ban while not scanning
    its writes is how the A360-1 gap was made."""
    paths = sorted(BACKEND / relative for relative in BI_OWNED)
    assert len(paths) > len(_bi_tree()), "BI_OWNED must reach beyond the two service/domain roots"
    return paths


# ---------------------------------------------------------------------------
# Import sites, with line numbers
# ---------------------------------------------------------------------------


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _literal(node: ast.expr) -> str | None:
    """The static text of an expression — ``_planes.string_value``, mirrored.

    Mirrored rather than imported so the placeholder spelling (``{name}`` /
    ``{?}`` for an f-string part that is not static) matches exactly; the
    meta-test below compares the two resolvers name for name, and a resolver
    that folded differently would show up as a phantom gap.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [_literal(part) for part in node.values]
        return "".join(part if part is not None else "{?}" for part in parts)
    if isinstance(node, ast.FormattedValue):
        return "{" + node.value.id + "}" if isinstance(node.value, ast.Name) else "{?}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    return None


def _keyword(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _literal(keyword.value)
    return None


def _package_of(module: str) -> str:
    if module.endswith(".__init__"):
        return module[: -len(".__init__")]
    return module.rpartition(".")[0]


def import_sites(path: Path, *, module: str | None = None) -> list[tuple[int, str]]:
    """``(lineno, dotted module)`` for every import in ``path``.

    Same resolution as ``_planes.imported_modules`` — absolute, relative
    (against the importing module's package), and ``importlib.import_module`` /
    ``__import__`` with a literal argument — but line-numbered, because an
    assertion that cannot say WHERE sends the reader grepping.
    ``test_the_site_collector_sees_what_the_shared_scanner_sees`` pins the two
    together.
    """
    if module is None:
        module = module_name(path) if path.is_relative_to(APP) else path.stem
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    package = _package_of(module)
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            sites.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            else:
                parts = package.split(".")
                anchor = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if not base:
                continue
            sites.append((node.lineno, base))
            sites.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
        elif isinstance(node, ast.Call) and _call_name(node) in {"import_module", "__import__"}:
            if not node.args or (name := _literal(node.args[0])) is None:
                continue
            if name.startswith("."):
                if (anchor_package := _keyword(node, "package")) is None:
                    continue
                level = len(name) - len(name.lstrip("."))
                parts = anchor_package.split(".")
                base_parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
                tail = name.lstrip(".")
                name = ".".join([*base_parts, *([tail] if tail else [])])
            sites.append((node.lineno, name))
    return sites


def _names_a_module(candidate: str, module: str) -> bool:
    """Whether ``candidate`` names ``module`` or something inside it."""
    return candidate == module or candidate.startswith(f"{module}.")


def bi_import_sites(path: Path, *, module: str | None = None) -> list[tuple[int, str]]:
    """Every import in ``path`` that reaches the BI plane."""
    return [
        (lineno, name)
        for lineno, name in import_sites(path, module=module)
        if any(_names_a_module(name, prefix) for prefix in BI_MODULE_PREFIXES)
    ]


def forbidden_bi_imports(path: Path, *, module: str | None = None) -> list[tuple[int, str]]:
    """BI imports in ``path`` that the seam allow-list does NOT permit."""
    allowed = _relative(path) in SEAM_IMPORTERS if path.is_relative_to(APP) else False
    return [
        (lineno, name)
        for lineno, name in bi_import_sites(path, module=module)
        if not (allowed and any(_names_a_module(name, seam) for seam in SEAM_MODULES))
    ]


# ---------------------------------------------------------------------------
# (a) the regulatory/live plane never imports BI
# ---------------------------------------------------------------------------


def test_the_regulatory_and_live_plane_never_imports_bi() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        relative = _relative(path)
        if relative in BI_OWNED or relative == MODEL_REGISTRY:
            continue
        offenders.extend(
            f"{relative}:{lineno} imports {name}" for lineno, name in forbidden_bi_imports(path)
        )
    assert offenders == [], (
        "The regulatory/live plane must not depend on BI (D-041). BI is a read "
        "projection of the canonical book, the live plane and the sealed runs; an "
        "import the other way makes a filed number depend on a mart. The only "
        f"permitted BI imports are {sorted(SEAM_MODULES)} from "
        f"{sorted(SEAM_IMPORTERS)}:\n  " + "\n  ".join(offenders)
    )


def test_every_hook_site_still_uses_the_seam() -> None:
    """An allow-list naming modules that no longer import the seam is a lie.

    The next reader widens the boundary by copying a dead entry, and a hook that
    has silently stopped enqueueing leaves a tenant's marts stale with no
    signal at all — which is the failure mode the seam exists to prevent.
    """
    missing = [
        relative
        for relative in sorted(SEAM_IMPORTERS)
        if not bi_import_sites(APP.parent / relative)
    ]
    assert missing == [], (
        f"SEAM_IMPORTERS names modules that import no BI seam: {missing}. Either the "
        "hook was removed (delete the entry and say why the mutation no longer "
        "changes a mart) or it was lost (restore the enqueue)."
    )


def test_the_model_registry_exemption_stays_a_registry_exemption() -> None:
    """``app/models/__init__.py`` may aggregate the BI MODELS and nothing more."""
    registry = APP.parent / MODEL_REGISTRY
    reached = {name for _, name in bi_import_sites(registry)}
    assert reached, f"{MODEL_REGISTRY} no longer aggregates app.models.bi"
    beyond = {name for name in reached if not _names_a_module(name, "app.models.bi")}
    assert beyond == set(), (
        f"{MODEL_REGISTRY} is exempt only as the SQLAlchemy metadata aggregator; it "
        f"must never import a BI service or domain module: {sorted(beyond)}"
    )


def test_the_import_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    """Each shape, individually, so a regression says which door re-opened."""
    samples = {
        "service": "from app.services.bi.mart_builder import refresh_bank_as_of\n",
        "domain": "from app.domain.bi.catalogue import catalogue\n",
        "model": "from app.models.bi import BiFactPositionDaily\n",
        "module import": "import app.services.bi.compiler\n",
        "importlib literal": (
            'import importlib\n\nm = importlib.import_module("app.services.bi.execution")\n'
        ),
        "assembled from literal parts": (
            'import importlib\n\nm = importlib.import_module("app.domain" + ".bi.extract")\n'
        ),
        # The seam itself, imported by a module that is not a hook site.
        "seam from the wrong module": "from app.services.bi.enqueue import enqueue_mart_refresh\n",
    }
    missed = []
    for label, body in samples.items():
        probe = tmp_path / "probe.py"
        probe.write_text(body, encoding="utf-8")
        if not forbidden_bi_imports(probe, module="app.services.regulatory_capital"):
            missed.append(label)
    assert missed == [], f"These imports walk through the guard: {missed}"


def test_the_guard_admits_the_seam_from_a_hook_site() -> None:
    """The allow-list must actually allow — otherwise rule (a) is untestable and
    the next agent weakens the rule rather than the allow-list."""
    for relative in sorted(SEAM_IMPORTERS):
        path = APP.parent / relative
        assert forbidden_bi_imports(path) == [], relative


def test_the_site_collector_sees_what_the_shared_scanner_sees() -> None:
    """This module's line-numbered collector must not drift from ``_planes``.

    ``_planes.imported_modules`` is the audited resolver (it grew the relative
    and dynamic-import cases in the 2026-08-22 case-plane audit). Line numbers
    are the only thing added here, so anything it can see must be visible here
    too, on every file either rule scans.
    """
    gaps: dict[str, set[str]] = {}
    for path in _app_modules():
        module = module_name(path)
        shared = imported_modules(path.read_text(encoding="utf-8"), module=module)
        mine = {name for _, name in import_sites(path, module=module)}
        if missed := shared - mine:
            gaps[_relative(path)] = missed
    assert gaps == {}, f"import_sites missed what _planes.imported_modules found: {gaps}"


# ---------------------------------------------------------------------------
# (b) BI never calls the official derivation
# ---------------------------------------------------------------------------


def derivation_references(path: Path) -> list[int]:
    """Lines where ``path`` NAMES ``derive_facts`` in code (never in a string).

    Prose is not a dependency: both ``app/services/bi/__init__.py`` and
    ``mart_builder.py`` say in their docstrings that they never call it, and a
    grep-based guard would convict exactly the modules that documented the rule.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name | ast.Attribute | ast.ImportFrom):
            continue
        names = (
            {node.id}
            if isinstance(node, ast.Name)
            else {node.attr}
            if isinstance(node, ast.Attribute)
            else {alias.name for alias in node.names}
        )
        if OFFICIAL_DERIVATION in names:
            lines.append(node.lineno)
    return sorted(lines)


def test_bi_never_reaches_for_the_official_derivation() -> None:
    offenders = [
        f"{_relative(path)}:{lineno}"
        for path in _bi_owned_tree()
        for lineno in derivation_references(path)
    ]
    assert offenders == [], (
        f"BI names {OFFICIAL_DERIVATION}() — the OFFICIAL/filing derivation, which "
        "REFUSES a book that does not reconcile. A mart is a read projection: it "
        "reads what the platform already derived (canonical rows, the live plane, "
        "sealed runs) and reports disagreement through the R-checks:\n  " + "\n  ".join(offenders)
    )


def test_no_bi_module_entered_the_derivation_allow_list() -> None:
    """Rule (b) from the other side.

    ``OFFICIAL_DERIVATION_CALLERS`` is the platform's list of modules that may
    mint filing evidence. Adding a BI module there would make the previous test
    pass by permission rather than by design, and it is a one-line change in a
    file no BI reviewer would be reading.
    """
    intruders = sorted(entry for entry in OFFICIAL_DERIVATION_CALLERS if entry in BI_OWNED)
    assert intruders == [], (
        f"{intruders} are BI modules on the official-derivation allow-list. BI never "
        "mints filing evidence — see test_derivation_plane_boundary.py."
    )


def test_the_derivation_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""A mart builder that mentions derive_facts in prose."""\n\n'
        "from app.services.fact_derivation import derive_facts\n\n\n"
        "def build(db, **kwargs):\n"
        "    return derive_facts(db, **kwargs)\n",
        encoding="utf-8",
    )
    assert derivation_references(probe) == [3, 7]

    prose_only = tmp_path / "prose.py"
    prose_only.write_text('"""This module never calls derive_facts."""\n', encoding="utf-8")
    assert derivation_references(prose_only) == []


# ---------------------------------------------------------------------------
# (c) BI writes bi_* tables and nothing else
# ---------------------------------------------------------------------------

#: class name -> table name, for every mapped class in the platform. Derived
#: from the registry so a new model is covered on the day it is written.
MAPPED_TABLES: dict[str, str] = {
    mapper.class_.__name__: mapper.class_.__tablename__ for mapper in Base.registry.mappers
}

#: The tables BI may write. Read off ``Base.metadata`` by prefix — never listed
#: — so a new mart needs no edit here, and a mart that is renamed out of the
#: ``bi_`` family stops being writable, which is the correct default.
BI_TABLES: frozenset[str] = frozenset(
    name for name in Base.metadata.tables if name.startswith("bi_")
)

#: ``Session`` methods that put an object on the unit of work. Checked ONLY when
#: the receiver is a session (see ``_WriteScanner.session_names``): ``set.add``,
#: ``dict.update`` and ``defaultdict`` accumulators share these names, and a
#: guard that convicts ``seen.add(hierarchy.id)`` gets silenced within a week.
SESSION_WRITES: frozenset[str] = frozenset(
    {
        "add",
        "add_all",
        "merge",
        "delete",
        "bulk_save_objects",
        "bulk_insert_mappings",
        "bulk_update_mappings",
    }
)

#: Statement constructors whose first argument is the table being written.
#: Checked only when the name is the one SQLAlchemy exported into this module —
#: ``matched.update(...)`` on a dict is not a SQL UPDATE.
WRITE_STATEMENTS: frozenset[str] = frozenset({"insert", "update", "delete"})

#: Variable names taken to be a session even with no annotation, so a probe (and
#: any module that drops its type hints) is still covered. The BI tree annotates
#: every one of them, which is where the real set comes from.
ASSUMED_SESSION_NAMES: frozenset[str] = frozenset({"db", "session"})

#: A name resolved to "nothing a model could be". Not an error on its own — it
#: is how ``x = cache.get(k)`` reads — but a write site that resolves ONLY to
#: this is unreadable and therefore a violation.
UNRESOLVED = "?"

#: ``(BI module, table)`` → why that module may write that non-``bi_*`` table
#: DIRECTLY. Every entry is a recorded decision, and
#: ``test_every_permitted_write_is_live`` fails the day an entry stops being
#: exercised, so the list cannot rot into a blanket permission.
PERMITTED_DIRECT_WRITES: dict[tuple[str, str], str] = {
    ("app/jobs/bi_commentary.py", "ai_commentary_drafts"): (
        "The AI tier's own ledger (D-191). A commentary draft is model output "
        "awaiting human review, not a mart figure, and it is keyed by the consent "
        "and approval references the AI gates require; it lives outside the bi_* "
        "family so the AI lane's retention and egress rules govern it rather than "
        "the mart rebuild. Written ONLY by the `ai`-lane job that holds the model key."
    ),
}

#: ``(imported helper, table)`` → why a BI module may write that table THROUGH
#: that helper. These are the platform's own seams; BI calls them exactly as
#: every other feature does and must never write the tables itself.
PERMITTED_MEDIATED_WRITES: dict[tuple[str, str], str] = {
    ("app.services.audit.record_event", "audit_events"): (
        "Every tenant mutation and every disclosure lands in the append-only audit "
        "trail through the one platform recorder; a BI route that saved a dashboard "
        "or served a feed without an audit row would be the defect."
    ),
    ("app.services.job_queue.enqueue", "jobs"): (
        "The one queue writer. BI enqueues its own job types (mart refresh, export, "
        "alert evaluation, subscription runs, AI drafts) and may never insert a "
        "`jobs` row by hand — the writer owns idempotency keys, lanes and reclaim."
    ),
    ("app.services.notifications.emit", "notifications"): (
        "A threshold alert that breaches is delivered to the bank's notification "
        "inbox through the platform's one emitter, beside every other platform "
        "notice, so the inbox's read/acknowledge lifecycle is not re-implemented "
        "for BI. Found by the A360-1 scan extension; not named in the audit."
    ),
    ("app.operator.deps.record_operator_action", "operator_audit_log"): (
        "Every staff-plane mutation lands in the append-only operator audit log "
        "(AGENTS.md, staff control plane); the operator backfill route is a staff "
        "mutation and records itself like every other. Found by the A360-1 scan "
        "extension; not named in the audit."
    ),
}


class _WriteScanner:
    """Resolves the write targets of one BI module.

    Deliberately small: per-function assignment tables, nearest-preceding
    resolution for a local name, and one level of parameter substitution from
    the call sites of the enclosing helper (``_insert_chunks(db, model, rows)``
    is the shape the builder actually uses, and a scanner that cannot see
    through it either passes vacuously or convicts correct code).
    """

    def __init__(
        self,
        path: Path,
        callsites: Mapping[str, list[ast.Call]] | None = None,
        *,
        permitted: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self.path = path
        self.relative = _relative(path) if path.is_relative_to(BACKEND) else path.name
        self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.callsites = dict(callsites or {})
        self.permitted = PERMITTED_DIRECT_WRITES if permitted is None else permitted
        self._functions: list[tuple[int, int, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._functions.append((node.lineno, node.end_lineno or node.lineno, node))
        self.session_names = self._session_names()
        self.sql_names, self.sql_aliases = self._sqlalchemy_names()

    def _session_names(self) -> frozenset[str]:
        """Names this module annotates as a ``Session``, plus the assumed two."""
        names = set(ASSUMED_SESSION_NAMES)
        for node in ast.walk(self.tree):
            annotated: list[tuple[str, ast.expr | None]] = []
            if isinstance(node, ast.arg):
                annotated = [(node.arg, node.annotation)]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                annotated = [(name, node.annotation)] if name else []
            for name, annotation in annotated:
                if annotation is not None and "Session" in ast.unparse(annotation):
                    names.add(name)
        return frozenset(names)

    def _sqlalchemy_names(self) -> tuple[frozenset[str], frozenset[str]]:
        """``from sqlalchemy import insert`` names, and ``import sqlalchemy as sa`` aliases."""
        names: set[str] = set()
        aliases: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
                names.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name.startswith("sqlalchemy")
                )
        return frozenset(names), frozenset(aliases)

    @staticmethod
    def _receiver(func: ast.expr) -> str | None:
        """The name the method is called ON, for ``x.add`` / ``self.db.add``."""
        if not isinstance(func, ast.Attribute):
            return None
        value = func.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
        return None

    def _is_session_write(self, node: ast.Call) -> bool:
        if _call_name(node) not in SESSION_WRITES:
            return False
        receiver = self._receiver(node.func)
        return receiver is not None and receiver in self.session_names

    def _is_statement_write(self, node: ast.Call) -> bool:
        called = _call_name(node)
        if called not in WRITE_STATEMENTS:
            return False
        if isinstance(node.func, ast.Name):
            return called in self.sql_names
        receiver = self._receiver(node.func)
        return receiver is not None and receiver in self.sql_aliases

    # -- scopes ------------------------------------------------------------
    def _enclosing(self, lineno: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        candidates = [
            (end - start, node) for start, end, node in self._functions if start <= lineno <= end
        ]
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def _assignments(self, scope: ast.AST) -> dict[str, list[tuple[int, ast.expr]]]:
        found: dict[str, list[tuple[int, ast.expr]]] = defaultdict(list)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign | ast.AnnAssign | ast.For):
                continue
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                targets, value = [node.target], node.iter
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id].append((node.lineno, value))
        return found

    def _parameters(self, scope: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int]:
        arguments = [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
        return {argument.arg: index for index, argument in enumerate(arguments)}

    # -- resolution --------------------------------------------------------
    def resolve(  # noqa: PLR0911 - one return per expression shape reads best
        self, node: ast.expr, *, lineno: int, depth: int = 0
    ) -> set[str]:
        """The model class names ``node`` can hold, or ``{UNRESOLVED}``."""
        if depth > 3:
            return {UNRESOLVED}
        if isinstance(node, ast.Call):
            called = _call_name(node)
            if called in MAPPED_TABLES:
                return {called}
            # ``model(**values)`` — the class is held in a local or a parameter,
            # which is how the builder's Type-1 upsert is written.
            if isinstance(node.func, ast.Name):
                through = self._resolve_name(node.func.id, lineno=lineno, depth=depth + 1)
                if through != {UNRESOLVED}:
                    return through
            # list(...) / .values() / sorted(...) — resolve what they wrap.
            if node.args:
                return self.resolve(node.args[0], lineno=lineno, depth=depth + 1)
            return {UNRESOLVED}
        if isinstance(node, ast.Attribute):
            return {node.attr} if node.attr in MAPPED_TABLES else {UNRESOLVED}
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            if not node.elts:
                return set()
            return {
                name
                for element in node.elts
                for name in self.resolve(element, lineno=lineno, depth=depth + 1)
            }
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self.resolve(node.elt, lineno=lineno, depth=depth + 1)
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id, lineno=lineno, depth=depth)
        return {UNRESOLVED}

    def _resolve_name(self, name: str, *, lineno: int, depth: int) -> set[str]:
        if name in MAPPED_TABLES:
            return {name}
        scope: ast.AST = self._enclosing(lineno) or self.tree
        assignments = self._assignments(scope)
        if bindings := [item for item in assignments.get(name, []) if item[0] <= lineno]:
            nearest = max(binding[0] for binding in bindings)
            return {
                resolved
                for line, value in bindings
                if line == nearest
                for resolved in self.resolve(value, lineno=line, depth=depth + 1)
            }
        function = self._enclosing(lineno)
        if function is not None and name in self._parameters(function):
            return self._from_callsites(function, name, depth=depth)
        return {UNRESOLVED}

    def _from_callsites(
        self, function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, *, depth: int
    ) -> set[str]:
        """What every BI call site of ``function`` passes for parameter ``name``."""
        index = self._parameters(function)[name]
        calls = self.callsites.get(function.name, [])
        if not calls:
            return {UNRESOLVED}
        resolved: set[str] = set()
        for call in calls:
            argument: ast.expr | None = None
            for keyword in call.keywords:
                if keyword.arg == name:
                    argument = keyword.value
            if argument is None and len(call.args) > index:
                argument = call.args[index]
            if argument is None:
                resolved.add(UNRESOLVED)
            else:
                resolved |= self.resolve(argument, lineno=call.lineno, depth=depth + 1)
        return resolved

    # -- the rule ----------------------------------------------------------
    def write_sites(self, scope: ast.AST | None = None) -> list[tuple[int, str, set[str]]]:
        """``(lineno, method, table names or UNRESOLVED)`` for every write in ``scope``."""
        sites: list[tuple[int, str, set[str]]] = []
        for node in ast.walk(scope if scope is not None else self.tree):
            if not isinstance(node, ast.Call):
                continue
            if self._is_session_write(node) or self._is_statement_write(node):
                targets = self.resolve(node.args[0], lineno=node.lineno) if node.args else set()
                tables = {MAPPED_TABLES.get(name, name) for name in targets} or {UNRESOLVED}
                sites.append((node.lineno, _call_name(node) or "?", tables))
        return sites

    def violations(self) -> list[str]:
        found: list[str] = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if self._is_session_write(node) or self._is_statement_write(node):
                found.extend(self._check(node, _call_name(node) or "?", node.args))
        return found

    def _check(self, node: ast.Call, called: str, args: Sequence[ast.expr]) -> list[str]:
        where = f"{self.relative}:{node.lineno}"
        if not args:
            return [f"{where} {called}() names no target"]
        targets = self.resolve(args[0], lineno=node.lineno)
        if not targets:
            return []  # a provably empty collection writes nothing
        offending = sorted(
            name
            for name in targets
            if name == UNRESOLVED
            or (
                MAPPED_TABLES.get(name, name) not in BI_TABLES
                and (self.relative, MAPPED_TABLES.get(name, name)) not in self.permitted
            )
        )
        if not offending:
            return []
        named = ", ".join(
            "an unreadable target" if name == UNRESOLVED else f"{name} ({MAPPED_TABLES[name]})"
            for name in offending
        )
        return [f"{where} {called}() writes {named}"]


def _bi_callsites() -> dict[str, list[ast.Call]]:
    """Every call made inside the BI tree, indexed by callee name.

    Used to substitute a helper's ``model`` parameter with what its callers
    actually pass. Indexed across the whole BI tree rather than per file so a
    helper and its callers may live in different modules.
    """
    index: dict[str, list[ast.Call]] = defaultdict(list)
    for path in _bi_owned_tree():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (name := _call_name(node)) is not None:
                index[name].append(node)
    return index


BI_CALLSITES: dict[str, list[ast.Call]] = _bi_callsites()


def write_violations(
    path: Path,
    *,
    callsites: Mapping[str, list[ast.Call]] | None = None,
    permitted: Mapping[tuple[str, str], str] | None = None,
) -> list[str]:
    return _WriteScanner(path, callsites, permitted=permitted).violations()


@pytest.mark.parametrize("path", _bi_owned_tree(), ids=_relative)
def test_bi_writes_only_bi_tables(path: Path) -> None:
    offenders = write_violations(path, callsites=BI_CALLSITES)
    assert offenders == [], (
        "BI may write only the bi_* marts (D-041), plus the named seams in "
        "PERMITTED_DIRECT_WRITES. Canonical, regulatory and live rows are written by "
        "ingestion, the pipelines and the governed registers; a mart that writes back "
        "makes an ingestion-traced row untraceable and a sealed run unreproducible. "
        f"Writable tables: {sorted(BI_TABLES)}\n  " + "\n  ".join(offenders)
    )


# -- mediated writes: a helper imported from outside BI that writes for it -----------------


def _module_file(dotted: str) -> Path | None:
    relative = Path(*dotted.split("."))
    for candidate in (BACKEND / relative.with_suffix(".py"), BACKEND / relative / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → dotted target, for plain ``import`` / ``from … import``."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted_callee(node: ast.Call, aliases: Mapping[str, str]) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in aliases:
        return aliases[func.id]
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in aliases
    ):
        return f"{aliases[func.value.id]}.{func.attr}"
    return None


def helper_write_targets(
    path: Path, function: str, *, depth: int = 0, seen: set[tuple[Path, str]] | None = None
) -> set[str]:
    """Tables ``function`` in ``path`` writes, following its own module's helpers.

    Two levels of same-module delegation are followed (``emit`` → ``_insert`` →
    ``db.add``); a delegation into a THIRD module is not, which the module
    docstring records as a scanning limit.
    """
    seen = set() if seen is None else seen
    if (path, function) in seen or depth > 2:
        return set()
    seen.add((path, function))
    scanner = _WriteScanner(path)
    definition = next(
        (
            node
            for node in ast.walk(scanner.tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
        ),
        None,
    )
    if definition is None:
        return set()
    local_functions = {
        node.name
        for node in ast.walk(scanner.tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    tables: set[str] = set()
    for _, _, written in scanner.write_sites(definition):
        tables |= written
    for node in ast.walk(definition):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in local_functions
            and node.func.id != function
        ):
            tables |= helper_write_targets(path, node.func.id, depth=depth + 1, seen=seen)
    return tables


def mediated_write_sites(path: Path) -> list[tuple[int, str, set[str]]]:
    """``(lineno, dotted helper, foreign tables)`` for every call in ``path`` to a
    helper imported from a non-BI ``app.*`` module whose body writes a table."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _import_aliases(tree)
    found: list[tuple[int, str, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_callee(node, aliases)
        if dotted is None or not dotted.startswith("app."):
            continue
        if any(_names_a_module(dotted, prefix) for prefix in BI_MODULE_PREFIXES):
            continue
        module, _, function = dotted.rpartition(".")
        target = _module_file(module)
        if target is None or _relative(target) in BI_OWNED:
            continue
        written = helper_write_targets(target, function)
        if written:
            found.append((node.lineno, dotted, written))
    return found


def mediated_write_violations(
    path: Path, *, permitted: Mapping[tuple[str, str], str] = PERMITTED_MEDIATED_WRITES
) -> list[str]:
    relative = _relative(path) if path.is_relative_to(BACKEND) else path.name
    violations: list[str] = []
    for lineno, dotted, written in mediated_write_sites(path):
        offending = sorted(
            table
            for table in written
            if table not in BI_TABLES and (dotted, table) not in permitted
        )
        if offending:
            named = ", ".join(
                "an unreadable target" if table == UNRESOLVED else table for table in offending
            )
            violations.append(f"{relative}:{lineno} {dotted}() writes {named}")
    return violations


@pytest.mark.parametrize("path", _bi_owned_tree(), ids=_relative)
def test_bi_writes_through_no_unlisted_helper(path: Path) -> None:
    offenders = mediated_write_violations(path)
    assert offenders == [], (
        "A BI module reaches a non-bi_* table through an imported helper that is not "
        "in PERMITTED_MEDIATED_WRITES. Relocating a write behind a helper is not a "
        "way round rule (c): either the write belongs to a platform seam every "
        "feature uses (name the (helper, table) pair with its reason) or it does not "
        "belong in BI:\n  " + "\n  ".join(offenders)
    )


def test_every_permitted_write_is_live() -> None:
    """A stale allow-list entry is a permission nobody is checking; fail it loudly.

    Both directions: every DIRECT entry names a module that actually writes that
    table, and every MEDIATED entry names a helper that actually writes that
    table AND is actually called from some BI module.
    """
    stale: list[str] = []
    for (relative, table), _ in PERMITTED_DIRECT_WRITES.items():
        written = {
            name
            for _, _, tables in _WriteScanner(BACKEND / relative, BI_CALLSITES).write_sites()
            for name in tables
        }
        if table not in written:
            stale.append(f"direct {relative} no longer writes {table}")
    reached: dict[str, set[str]] = defaultdict(set)
    for path in _bi_owned_tree():
        for _, dotted, written in mediated_write_sites(path):
            reached[dotted] |= written
    for (dotted, table), _ in PERMITTED_MEDIATED_WRITES.items():
        if table not in reached.get(dotted, set()):
            stale.append(f"mediated {dotted} is not called from BI or no longer writes {table}")
    assert stale == [], stale
    assert all(reason.strip() for reason in PERMITTED_DIRECT_WRITES.values())
    assert all(reason.strip() for reason in PERMITTED_MEDIATED_WRITES.values())


def test_every_bi_owned_module_is_in_the_write_scan() -> None:
    """The A360-1 gap, pinned structurally: rule (a)'s exemption set and rule (c)'s
    scan set are the SAME set. Exempting a module from the import ban without
    scanning its writes is how a non-bi_* write hides."""
    scanned = {_relative(path) for path in _bi_owned_tree()}
    assert scanned == BI_OWNED
    assert "app/features/read_bi.py" in scanned
    assert "app/jobs/bi_commentary.py" in scanned
    assert "app/operator/features/bi_backfill.py" in scanned


def test_a_planted_canonical_write_in_a_feature_module_is_convicted(tmp_path: Path) -> None:
    """The defect the audit named, planted: a canonical write appended to a copy
    of a real feature-plane BI module must be convicted by the same scanner the
    parametrised rule runs, and that module must be in the scanned set."""
    source = (BACKEND / "app/features/read_bi.py").read_text(encoding="utf-8")
    probe = tmp_path / "read_bi.py"
    probe.write_text(
        source + "\n\ndef _write_back(db: Session) -> None:\n"
        "    db.add(CanonicalPosition(balance=1))\n",
        encoding="utf-8",
    )
    found = write_violations(probe, callsites=BI_CALLSITES)
    assert any("CanonicalPosition" in message for message in found), found
    assert BACKEND / "app/features/read_bi.py" in _bi_owned_tree()


def test_the_mediated_write_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    """Fired three ways: the real audit recorder with the allow-list emptied; the
    real queue writer reached through a module alias; and a helper whose table
    the allow-list names for a DIFFERENT helper."""
    samples = {
        "audit recorder, unlisted": (
            "from app.services.audit import record_event\n\n\n"
            "def f(db, ctx):\n"
            "    record_event(db, ctx, event_type='x', entity_type='y', entity_id='z')\n",
            {},
            "audit_events",
        ),
        "queue writer through a module alias, unlisted": (
            "from app.services import job_queue\n\n\n"
            "def f(db):\n    job_queue.enqueue(db, 'bi_export', {})\n",
            {},
            "jobs",
        ),
        "table permitted for another helper only": (
            "from app.services import job_queue\n\n\n"
            "def f(db):\n    job_queue.enqueue(db, 'bi_export', {})\n",
            {("app.services.audit.record_event", "jobs"): "wrong helper"},
            "jobs",
        ),
    }
    missed = []
    for label, (body, permitted, table) in samples.items():
        probe = tmp_path / "probe.py"
        probe.write_text(body, encoding="utf-8")
        found = mediated_write_violations(probe, permitted=permitted)
        if not found or not any(table in message for message in found):
            missed.append(f"{label}: {found}")
    assert missed == [], f"These mediated writes walk through the guard: {missed}"


def test_the_mediated_write_guard_admits_the_listed_seams(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.services import job_queue\n"
        "from app.services.audit import record_event\n\n\n"
        "def f(db, ctx):\n"
        "    record_event(db, ctx, event_type='x', entity_type='y', entity_id='z')\n"
        "    job_queue.enqueue(db, 'bi_export', {})\n",
        encoding="utf-8",
    )
    assert mediated_write_violations(probe) == []


def test_the_writable_table_set_is_derived_from_the_bi_models() -> None:
    """The set is read off ``Base.metadata``, so it tracks new marts by itself.

    Two ways it could go vacuous, both pinned: emptying (a renamed prefix would
    make every BI write a violation, which is loud, but a renamed TABLE that
    kept the prefix would silently stop being checked), and widening (a non-BI
    table must never be in it, or the rule admits the thing it forbids).
    """
    # The BI plane is modelled across SEVERAL modules since Phase 3. Reading only
    # `app.models.bi` would leave saved content, alerts and subscriptions outside
    # the writable set while `BI_TABLES` (derived from the metadata) contains
    # them, so every write to one would be convicted as a plane violation.
    from app.models import bi as bi_models  # noqa: PLC0415 - read each module's own list
    from app.models import bi_content, bi_notifications  # noqa: PLC0415

    modules = (bi_models, bi_content, bi_notifications)
    declared = {
        value.__tablename__
        for module in modules
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Base) and hasattr(value, "__tablename__")
    }
    assert declared, "no app.models.bi* module declares a table"
    assert declared == BI_TABLES, (
        "every table the BI model modules declare must be writable, and nothing "
        f"else: {sorted(declared ^ BI_TABLES)}"
    )
    assert all(table.startswith("bi_") for table in BI_TABLES)


def test_the_write_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    samples = {
        "add a canonical row": (
            "def f(db):\n    db.add(CanonicalPositionSnapshot(balance=1))\n",
            "CanonicalPositionSnapshot",
        ),
        "add through a local": (
            "def f(db):\n    row = CurrentFinancialFact(amount=1)\n    db.add(row)\n",
            "CurrentFinancialFact",
        ),
        "add_all a comprehension": (
            "def f(db, rows):\n    db.add_all([BankFinancialFact(amount=r) for r in rows])\n",
            "BankFinancialFact",
        ),
        "delete a regulatory table": (
            "from sqlalchemy import delete\n\n\n"
            "def f(db):\n    db.execute(delete(RegulatoryRun))\n",
            "RegulatoryRun",
        ),
        "update a canonical table": (
            "from sqlalchemy import update\n\n\n"
            "def f(db):\n    db.execute(update(CanonicalPosition).values(x=1))\n",
            "CanonicalPosition",
        ),
        "unreadable target": (
            "def f(db, thing):\n    db.add(thing)\n",
            "an unreadable target",
        ),
    }
    missed = []
    for label, (body, expected) in samples.items():
        probe = tmp_path / "probe.py"
        probe.write_text(body, encoding="utf-8")
        found = write_violations(probe)
        if not found or not any(expected in message for message in found):
            missed.append(f"{label}: {found}")
    assert missed == [], f"These writes walk through the guard: {missed}"


def test_the_direct_allow_list_admits_only_its_own_module(tmp_path: Path) -> None:
    """``ai_commentary_drafts`` is writable from the AI job and from nowhere else."""
    body = "def f(db):\n    db.add(AiCommentaryDraft(mode='x'))\n"
    elsewhere = tmp_path / "probe.py"
    elsewhere.write_text(body, encoding="utf-8")
    assert write_violations(elsewhere), "the AI ledger is writable from an unlisted module"
    real = _WriteScanner(BACKEND / "app/jobs/bi_commentary.py", BI_CALLSITES)
    assert real.violations() == []
    assert any("ai_commentary_drafts" in tables for _, _, tables in real.write_sites())


def test_the_write_guard_does_not_convict_a_set_or_a_dict(tmp_path: Path) -> None:
    """``seen.add(x)`` and ``matched.update(x)`` are not writes.

    They are the reason the rule discriminates on the RECEIVER: the catalogue
    validator accumulates ids in a ``set`` and the authorization evaluator
    accumulates matched binding ids in a ``dict``, and the first draft of this
    guard convicted both. A guard that cries wolf on correct code is deleted,
    not fixed.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(hierarchies, decisions):\n"
        "    seen: set[str] = set()\n"
        "    matched: dict[str, None] = {}\n"
        "    for hierarchy in hierarchies:\n"
        "        seen.add(hierarchy.id)\n"
        "    for decision in decisions:\n"
        "        matched.update(dict.fromkeys(decision.ids))\n"
        "    return seen, matched\n",
        encoding="utf-8",
    )
    assert write_violations(probe) == []


def test_the_write_guard_admits_the_shapes_the_builder_uses(tmp_path: Path) -> None:
    """The converse: the guard must not convict correct code, or the next agent
    silences it. These are the three shapes the mart builder is written in."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from sqlalchemy import delete, insert\n\n\n"
        "def _insert_chunks(db, model, rows):\n"
        "    db.execute(insert(model), rows)\n\n\n"
        "def _upsert(db, model, existing, values):\n"
        "    current = existing.get('k')\n"
        "    if current is None:\n"
        "        current = model(**values)\n"
        "        db.add(current)\n\n\n"
        "def _replace(db):\n"
        "    db.execute(delete(BiFactPositionDaily))\n"
        "    _insert_chunks(db, BiFactPositionDaily, [])\n"
        "    _upsert(db, BiDimBranch, {}, {})\n",
        encoding="utf-8",
    )
    callsites: dict[str, list[ast.Call]] = defaultdict(list)
    for node in ast.walk(ast.parse(probe.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and (name := _call_name(node)) is not None:
            callsites[name].append(node)
    assert write_violations(probe, callsites=callsites) == []


# ---------------------------------------------------------------------------
# (d) app/domain/bi is pure
# ---------------------------------------------------------------------------

#: Prefixes that ARE application state. ``app.core.config`` is included
#: deliberately: a pure module that reads a setting is configurable behaviour,
#: which the golden suites cannot pin.
STATEFUL_PREFIXES: tuple[str, ...] = (
    "app.services",
    "app.models",
    "app.db",
    "app.api",
    "app.features",
    "app.adapters",
    "app.core.config",
    "app.jobs",
    "app.operator",
    "app.storage",
    "sqlalchemy.orm",
)


def impurities(path: Path, *, module: str | None = None) -> list[str]:
    return [
        f"{_relative(path) if path.is_relative_to(BACKEND) else path.name}:{lineno} {name}"
        for lineno, name in import_sites(path, module=module)
        if name.startswith(STATEFUL_PREFIXES) or name == "sqlalchemy"
    ]


def test_the_bi_domain_layer_imports_no_application_state() -> None:
    offenders = [message for path in _bi_files(BI_DOMAIN_ROOT) for message in impurities(path)]
    assert offenders == [], (
        "app/domain/bi must stay pure (the BI restatement of the app/domain rule). "
        "The catalogue and the row extractors are what the golden suites pin and "
        "what a second product segment reuses; a Session or a settings read makes "
        "them untestable in isolation:\n  " + "\n  ".join(offenders)
    )


def test_the_purity_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    samples = (
        "from app.models.bi import BiFactPositionDaily\n",
        "from sqlalchemy.orm import Session\n",
        "from app.core.config import get_settings\n",
        "from app.services.bi import mart_builder\n",
        "import app.db.session\n",
    )
    missed = []
    for body in samples:
        probe = tmp_path / "probe.py"
        probe.write_text(body, encoding="utf-8")
        if not impurities(probe, module="app.domain.bi.extract"):
            missed.append(body.strip())
    assert missed == [], f"These reach application state unnoticed: {missed}"


# ---------------------------------------------------------------------------
# (e) the seam is thin — which is what makes rule (a)'s allow-list safe
# ---------------------------------------------------------------------------

#: What the seam may never pull into the request path and the core worker.
#:
#: ``app.models.bi`` is NOT here, and that is D-043: the recovery sweep's "is
#: this bank due" read moved INTO the seam precisely so ``scheduler.py`` imports
#: no BI model, and importing the two models costs the hot path nothing because
#: ``app/models/__init__.py`` already loads them for every consumer of
#: ``app.models``. The cost the seam must avoid is the BUILDER — the catalogue,
#: the compiler and the handler tree.
SEAM_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "app.services.bi.mart_builder",
    "app.services.bi.compiler",
    "app.services.bi.execution",
    "app.services.bi.partitions",
    "app.domain.bi",
    "app.jobs",
)


@pytest.mark.parametrize("seam", sorted(SEAM_MODULES))
def test_the_seam_pulls_in_no_builder(seam: str) -> None:
    path = APP / Path(*seam.split(".")[1:]).with_suffix(".py")
    offenders = [
        f"{_relative(path)}:{lineno} {name}"
        for lineno, name in import_sites(path)
        if name.startswith(SEAM_FORBIDDEN_PREFIXES)
    ]
    assert offenders == [], (
        f"{seam} is the enqueue seam the regulatory/live plane is allowed to import "
        "(D-041). That permission rests on it being thin: ingestion, the pipelines "
        "and the register triggers run in the request path and the core worker, and "
        "must not load the mart builder:\n  " + "\n  ".join(offenders)
    )


def test_the_version_constant_module_imports_nothing() -> None:
    """``versions.py`` is imported by both planes, so it must be a leaf."""
    path = APP / "services" / "bi" / "versions.py"
    imported = {name for _, name in import_sites(path)}
    assert imported <= {"__future__", "__future__.annotations"}, imported
