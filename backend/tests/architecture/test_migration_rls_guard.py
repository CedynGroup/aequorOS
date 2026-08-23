"""No migration may write to a tenant table without being able to see it.

Audit finding P0-18. On an RLS-forced Postgres a tenant-scoped role's ``UPDATE``
against a FORCE-RLS table matches zero rows and reports success. Alembic runs as
whichever role owns ``DATABASE_URL``, which in production is deliberately
tenant-scoped, so a cross-tenant data migration can be stamped applied while
having changed nothing. That is what happened to the BoG return-code recode in
``202608150013``; the remediation it shipped with was a docstring asking a human
to remember.

``app.db.session.force_rls_suspended`` makes the write work under any owning
role and raise under any other. This test is the half that makes the next one
impossible to forget: a migration that writes to a tenant-scoped table without
routing through it fails here, by name, with the fix in the message.

The check is STRUCTURAL (2026-08-22). It used to be
``"force_rls_suspended" in source`` — a whole-file substring test, so a
docstring reading *"no need for force_rls_suspended here"* switched the scanner
off for every statement in the file. That is the sentence someone writes while
making this exact mistake, and it was the strongest way to disarm the guard.
What is asked now is whether the write EXECUTES inside a block that suspends
THAT table:

* the write must sit in the ``with`` body, or in a module-local helper every
  one of whose call sites sits in such a body (`202608150013` is factored that
  way and must not be convicted for it);
* the block must NAME the table — ``force_rls_suspended`` lifts FORCE only for
  the tables it is given, so a write nested inside a block that suspends a
  different table is just as blind;
* writes are found in SQL text (including f-strings and ``"a" + "b"``
  concatenations), in ``op.bulk_insert``, and in SQLAlchemy Core
  ``update``/``insert``/``delete`` — none of which the textual scanner saw.

WHAT THIS GUARD DOES NOT DO
---------------------------
* **Only static SQL.** A statement assembled at runtime, read from a file, or
  built by a helper in ANOTHER module is invisible.
* **Only module-local call following.** A write performed by a function
  imported from elsewhere is not traced into.
* **No execution-order reasoning.** Guardedness is decided lexically plus one
  call-graph hop; a conditional or a loop that skips the block is not modelled.
* **Unreadable guard arguments are trusted.** When the suspended-table list
  cannot be resolved statically the block is treated as covering everything.
  That is a deliberate false-negative: a scanner that invents offences stops
  being read.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.db.base import Base

MIGRATIONS = Path(__file__).parents[2] / "alembic" / "versions"

_DML = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+(\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

#: Migrations that predate the guard and write to tenant tables without it.
#: FROZEN — this list may shrink, never grow. Each entry is a migration whose
#: DML either targets rows that cannot exist yet at that point in the chain (the
#: table is created by the same migration) or was verified applied on the
#: primary. A new migration must use ``force_rls_suspended`` instead of being
#: added here.
LEGACY_UNGUARDED: frozenset[str] = frozenset(
    {
        "202606080001_financial_cash_flows.py",
        "202607120001_financial_covenants.py",
        "202607160002_forecast_runs.py",
        "202607170001_irr_fx_ftp_foundation.py",
        "202607170003_hedge_swap_position_types.py",
        "202607170004_api_push_source_system.py",
        "202607170006_market_data_entities.py",
        "202607170010_database_direct_connections.py",
        "202607200015_sso_connections.py",
        "202607240023_regulatory_reporting_basis_dbk_settings.py",
        "202607240024_platform_public_ids.py",
        "202607240025_platform_ids_become_primary_keys.py",
        "202607250029_typed_signature_fields.py",
        "202608090043_market_desk_foundations.py",
        "202608160015_xlsx_working_artifact_kind.py",
        "202608190017_live_state_decoupled_from_reporting_period.py",
        "202608190018_institution_types_registry.py",
    }
)


def tenant_scoped_tables() -> frozenset[str]:
    """Tables carrying ``organization_id``: the ones RLS policies filter."""
    return frozenset(
        table.name for table in Base.metadata.tables.values() if "organization_id" in table.columns
    )


#: Calls that write rows without any SQL string: ``op.bulk_insert(table, rows)``
#: and the SQLAlchemy Core constructors. The textual scanner saw none of these.
_CORE_WRITERS: frozenset[str] = frozenset({"update", "insert", "delete", "bulk_insert"})


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Nodes that are docstrings, so prose is never read as a statement."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


def _part_text(part: ast.expr) -> str:
    """One f-string segment, with ``{table}`` preserved as a resolvable name."""
    if isinstance(part, ast.Constant) and isinstance(part.value, str):
        return part.value
    if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
        return "{" + part.value.id + "}"
    return "{?}"


def _static_text(node: ast.expr) -> str | None:
    """The static text of an expression, folding ``"a" + "b"``.

    SQL split across concatenated string parts was a demonstrated evasion of
    the whole-file substring scanner this replaces.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(_part_text(part) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _static_text(node.left), _static_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _sql_strings(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """``(node, text)`` for every SQL-bearing string, docstrings excluded.

    The node travels with the text because guardedness is now a question about
    WHERE the statement sits, not whether a word appears in the file.
    """
    docstrings = _docstring_ids(tree)
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if id(node) not in docstrings and isinstance(node.value, str):
                found.append((node, node.value))
        elif isinstance(node, ast.JoinedStr):
            found.append((node, "".join(_part_text(part) for part in node.values)))
        elif isinstance(node, ast.BinOp) and (text := _static_text(node)) is not None:
            found.append((node, text))
    return found


def _literal_strings(tree: ast.AST) -> list[str]:
    """Backwards-compatible text-only view of :func:`_sql_strings`."""
    return [text for _, text in _sql_strings(tree)]


def _name_bindings(tree: ast.AST) -> dict[str, set[str]]:
    """Module constants and ``for`` targets that can stand in for a table name.

    Also resolves ``T = sa.table("x", ...)`` / ``sa.Table("x", meta, ...)``, so
    a SQLAlchemy Core write through ``T`` names a table this scanner knows.
    """
    bindings: dict[str, set[str]] = {}

    def literal(value: ast.expr) -> set[str]:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return {value.value}
        if isinstance(value, ast.Tuple | ast.List):
            names: set[str] = set()
            for element in value.elts:
                # `GUARDED = ("a", *OTHERS)` — the splat is how a real migration
                # composes its guarded-table tuple, and dropping it made the
                # spliced-in tables look unguarded.
                if isinstance(element, ast.Starred) and isinstance(element.value, ast.Name):
                    names |= bindings.get(element.value.id, set())
                elif isinstance(element, ast.Constant) and isinstance(element.value, str):
                    names.add(element.value)
            return names
        if (
            isinstance(value, ast.Call)
            and _called_name(value) in {"table", "Table"}
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            return {value.args[0].value}
        return set()

    # Two passes, because a splat can reference a name bound later in the walk.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (values := literal(node.value)):
                        bindings.setdefault(target.id, set()).update(values)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
                and (values := literal(node.value))
            ):
                bindings.setdefault(node.target.id, set()).update(values)

    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            source = node.iter
            if isinstance(source, ast.Name):
                bindings.setdefault(node.target.id, set()).update(bindings.get(source.id, set()))
            elif values := literal(source):
                bindings.setdefault(node.target.id, set()).update(values)
    return bindings


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _resolve(value: ast.expr, bindings: dict[str, set[str]]) -> set[str] | None:
    """Table names an expression can denote, or ``None`` if it is not static."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, ast.Name):
        return bindings.get(value.id)
    if (
        isinstance(value, ast.Call)
        and _called_name(value) in {"table", "Table"}
        and value.args
        and isinstance(value.args[0], ast.Constant)
        and isinstance(value.args[0].value, str)
    ):
        return {value.args[0].value}
    if isinstance(value, ast.Attribute):
        return _resolve(value.value, bindings)
    return None


Region = tuple[set[int], set[str] | None]


def _guarded_regions(tree: ast.AST, bindings: dict[str, set[str]]) -> list[Region]:
    """Each ``with force_rls_suspended(...)`` body, and the tables it suspends.

    Returns ``(node ids inside the body, covered tables)``. ``None`` for the
    covered set means "not statically resolvable" and is treated as covering
    everything, so an unreadable guard never produces a false accusation.

    Only the BODY is collected, never the ``with`` item itself: this is what
    makes the check structural. A docstring or a comment mentioning
    ``force_rls_suspended`` contributes no region at all, and a write placed
    after the block is in no region either.
    """
    regions: list[Region] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With | ast.AsyncWith):
            continue
        covered: set[str] | None = set()
        guarded = False
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call) or _called_name(call) != "force_rls_suspended":
                continue
            guarded = True
            if covered is None:
                continue
            for argument in call.args[1:]:  # args[0] is the connection
                target = argument.value if isinstance(argument, ast.Starred) else argument
                names = _resolve(target, bindings)
                if names is None:
                    covered = None
                    break
                covered |= names
        if not guarded:
            continue
        regions.append((_region_nodes(tree, node.body), covered))
    return regions


def _local_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _region_nodes(tree: ast.AST, body: list[ast.stmt]) -> set[int]:
    """Every node that EXECUTES inside a guarded block, calls included.

    Lexical containment alone is not enough: `202608150013` performs its
    rewrite in a module-local helper invoked from inside the block, which is
    ordinary factoring and must not read as an unguarded write. A call to a
    local function therefore pulls that function's body into the region — but
    only when EVERY call site of that function in the module is itself inside a
    guarded block, so a helper that is also called from open code stays
    convicted.
    """
    functions = _local_functions(tree)
    inside_any_guard: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With | ast.AsyncWith):
            continue
        if not any(
            isinstance(item.context_expr, ast.Call)
            and _called_name(item.context_expr) == "force_rls_suspended"
            for item in node.items
        ):
            continue
        for statement in node.body:
            inside_any_guard.update(id(child) for child in ast.walk(statement))

    inside = {id(child) for statement in body for child in ast.walk(statement)}
    for _ in range(len(functions) + 1):
        reachable = {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and id(node) in inside
            and (name := _called_name(node)) in functions
        }
        grown = False
        for name in reachable:
            call_sites = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _called_name(node) == name
            ]
            if not all(id(site) in inside_any_guard for site in call_sites):
                continue
            for statement in functions[name].body:
                for child in ast.walk(statement):
                    if id(child) not in inside:
                        inside.add(id(child))
                        grown = True
        if not grown:
            break
    return inside


def _core_writes(tree: ast.AST, bindings: dict[str, set[str]]) -> list[tuple[ast.AST, str]]:
    """Row writes expressed as calls rather than SQL text.

    ``op.bulk_insert(table, rows)`` and ``sa.update(t)`` / ``t.update()`` carry
    no DML string at all, so the textual scanner could not see them.
    """
    writes: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) not in _CORE_WRITERS:
            continue
        target: ast.expr | None = node.args[0] if node.args else None
        if target is None and isinstance(node.func, ast.Attribute):
            target = node.func.value  # `t.update()`
        if target is None or (names := _resolve(target, bindings)) is None:
            continue
        writes.extend((node, table) for table in names)
    return writes


def dml_targets(source: str) -> set[str]:
    """Table names this migration writes rows to, by any means."""
    tree = ast.parse(source)
    bindings = _name_bindings(tree)
    targets: set[str] = set()
    for _, statement in _sql_strings(tree):
        for raw in _DML.findall(statement):
            if raw.startswith("{"):
                targets.update(bindings.get(raw.strip("{}"), set()))
            else:
                targets.add(raw)
    targets.update(table for _, table in _core_writes(tree, bindings))
    return targets


def unguarded_tenant_writes(source: str, tenant_tables: frozenset[str]) -> set[str]:
    """Tenant tables written OUTSIDE a ``force_rls_suspended`` block covering them.

    Structural, not textual. The rule this replaces was
    ``"force_rls_suspended" in source`` — a whole-file substring test that a
    docstring saying *"no need for force_rls_suspended here"* disarmed for the
    entire file, which is exactly the sentence someone writes while making this
    mistake.
    """
    tree = ast.parse(source)
    bindings = _name_bindings(tree)
    regions = _guarded_regions(tree, bindings)

    def guarded(node: ast.AST, table: str) -> bool:
        return any(
            id(node) in inside and (covered is None or table in covered)
            for inside, covered in regions
        )

    offenders: set[str] = set()
    for node, statement in _sql_strings(tree):
        for raw in _DML.findall(statement):
            tables = bindings.get(raw.strip("{}"), set()) if raw.startswith("{") else {raw}
            offenders.update(table for table in tables & tenant_tables if not guarded(node, table))
    for node, table in _core_writes(tree, bindings):
        if table in tenant_tables and not guarded(node, table):
            offenders.add(table)
    return offenders


def test_no_new_migration_writes_to_a_tenant_table_unguarded() -> None:
    tenant_tables = tenant_scoped_tables()
    # Audit 2026-08-22 D-14: ``tenant_scoped_tables`` reads ``Base.metadata``,
    # which is populated only because a conftest imports ``app.main`` during
    # collection. Run under a different conftest and the set is EMPTY, every
    # write matches nothing, ``offenders == {}`` trivially, and this file passes
    # while checking nothing. Assert the census is non-empty so the guard cannot
    # pass vacuously.
    assert len(tenant_tables) > 100, (
        "the tenant-table census is empty or implausibly small "
        f"({len(tenant_tables)}); Base.metadata was not populated, so this guard "
        "would pass without checking anything"
    )
    offenders: dict[str, set[str]] = {}

    for path in sorted(MIGRATIONS.glob("*.py")):
        if path.name in LEGACY_UNGUARDED:
            continue
        if written := unguarded_tenant_writes(path.read_text(), tenant_tables):
            offenders[path.name] = written

    assert offenders == {}, (
        "These migrations write to FORCE-RLS tenant tables without suspending "
        "row-level security, so under a tenant-scoped alembic role they will "
        "match zero rows and silently succeed: "
        + "; ".join(f"{name} -> {sorted(tables)}" for name, tables in sorted(offenders.items()))
        + ". Wrap the statements in `app.db.session.force_rls_suspended`."
    )


def test_the_legacy_allowlist_only_names_migrations_that_exist() -> None:
    """A stale entry would silently re-open the hole for a renamed migration."""
    present = {path.name for path in MIGRATIONS.glob("*.py")}

    assert present >= LEGACY_UNGUARDED, f"Unknown allowlist entries: {LEGACY_UNGUARDED - present}"


def test_the_guard_catches_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the scanner reports rather than merely being present."""
    violation = tmp_path / "202699010001_sneaky_tenant_rewrite.py"
    violation.write_text(
        '''"""A migration that rewrites tenant rows with no RLS suspension.

    Prose mentioning UPDATE regulatory_packages must not be picked up.
    """

from alembic import op

TABLES = ("regulatory_packages",)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"UPDATE {table} SET return_code = 'X' WHERE return_code = 'Y'")
'''
    )

    written = unguarded_tenant_writes(violation.read_text(), tenant_scoped_tables())

    assert written == {"regulatory_packages"}


def test_the_guard_accepts_the_same_migration_once_it_is_wrapped(tmp_path: Path) -> None:
    guarded = tmp_path / "202699010002_guarded_tenant_rewrite.py"
    guarded.write_text(
        """from alembic import op

from app.db.session import force_rls_suspended

TABLES = ("regulatory_packages",)


def upgrade() -> None:
    with force_rls_suspended(op.get_bind(), *TABLES):
        for table in TABLES:
            op.execute(f"UPDATE {table} SET return_code = 'X' WHERE return_code = 'Y'")
"""
    )

    assert unguarded_tenant_writes(guarded.read_text(), tenant_scoped_tables()) == set()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("UPDATE regulatory_packages SET a = 1", {"regulatory_packages"}),
        ("DELETE FROM jobs WHERE id = 1", {"jobs"}),
        ("INSERT INTO users (id) VALUES (1)", {"users"}),
        ("SELECT count(*) FROM regulatory_packages", set()),
    ],
)
def test_scanner_recognises_writes_and_ignores_reads(statement: str, expected: set[str]) -> None:
    source = f'from alembic import op\n\n\ndef upgrade() -> None:\n    op.execute("{statement}")\n'

    assert dml_targets(source) & tenant_scoped_tables() == expected


# --------------------------------------------------------------------------
# The 2026-08-22 audit's evasions. Each one disarmed the textual scanner.
# --------------------------------------------------------------------------

_HEADER = "from alembic import op\nimport sqlalchemy as sa\n\n"


def _scan(body: str, header: str = _HEADER) -> set[str]:
    return unguarded_tenant_writes(header + body, tenant_scoped_tables())


def test_a_docstring_mentioning_the_guard_no_longer_disarms_the_file() -> None:
    """The sharpest edge of the old rule, and the reason D2 was filed.

    ``"force_rls_suspended" in source`` was a WHOLE-FILE substring test, so a
    migration whose docstring said *"no need for force_rls_suspended here"*
    switched the scanner off for every statement in the file — which is exactly
    the sentence someone writes while making this mistake.
    """
    body = (
        "def upgrade() -> None:\n"
        '    """Recode the return codes.\n\n'
        "    No need for force_rls_suspended here, these rows are global.\n"
        '    """\n'
        "    op.execute(\"UPDATE regulatory_packages SET return_code = 'X'\")\n"
    )

    assert _scan(body) == {"regulatory_packages"}
    assert "force_rls_suspended" in _HEADER + body  # the old rule saw this and stopped


def test_a_comment_mentioning_the_guard_no_longer_disarms_the_file() -> None:
    """Same hole, spelled as a comment. Comments never reach the AST at all,
    but they are in `source`, which is all the old rule read."""
    body = (
        "def upgrade() -> None:\n"
        "    # force_rls_suspended is not needed: this runs under the worker role.\n"
        "    op.execute(\"UPDATE regulatory_packages SET return_code = 'X'\")\n"
    )

    assert _scan(body) == {"regulatory_packages"}


def test_guarding_one_table_does_not_cover_a_write_to_another() -> None:
    """A real block plus a write placed outside it.

    The old rule asked only whether the WORD appeared anywhere in the file, so a
    genuine, correct suspension of table A licensed an unguarded write to table
    B three lines below it.
    """
    body = (
        "from app.db.session import force_rls_suspended\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), 'regulatory_packages'):\n"
        "        op.execute(\"UPDATE regulatory_packages SET return_code = 'X'\")\n"
        "    op.execute('DELETE FROM regulatory_runs WHERE status = %s')\n"
    )

    assert _scan(body) == {"regulatory_runs"}


def test_a_write_inside_a_block_that_does_not_name_its_table_is_convicted() -> None:
    """`force_rls_suspended` lifts FORCE only for the tables it is GIVEN.

    A write nested inside a block that suspends a different table is as blind as
    one outside it, and lexical containment alone would have let it through.
    """
    body = (
        "from app.db.session import force_rls_suspended\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), 'regulatory_packages'):\n"
        "        op.execute(\"UPDATE regulatory_runs SET status = 'sealed'\")\n"
    )

    assert _scan(body) == {"regulatory_runs"}


def test_sqlalchemy_core_writes_are_seen_although_they_carry_no_sql_string() -> None:
    """`sa.update(t)` and `t.delete()` produce no DML text to grep for."""
    core = (
        "PACKAGES = sa.table('regulatory_packages', sa.column('return_code'))\n\n\n"
        "def upgrade() -> None:\n"
        "    op.execute(sa.update(PACKAGES).values(return_code='X'))\n"
    )
    method = (
        "PACKAGES = sa.table('regulatory_packages', sa.column('return_code'))\n\n\n"
        "def upgrade() -> None:\n"
        "    op.execute(PACKAGES.delete())\n"
    )

    assert _scan(core) == {"regulatory_packages"}
    assert _scan(method) == {"regulatory_packages"}


def test_bulk_insert_into_a_tenant_table_is_seen() -> None:
    """`op.bulk_insert` writes rows and names no table in any string."""
    body = (
        "PACKAGES = sa.table('regulatory_packages', sa.column('return_code'))\n\n\n"
        "def upgrade() -> None:\n"
        "    op.bulk_insert(PACKAGES, [{'return_code': 'X'}])\n"
    )

    assert _scan(body) == {"regulatory_packages"}


def test_sql_split_across_concatenated_parts_is_reassembled() -> None:
    """`"UPDATE regulatory_pack" + "ages SET ..."` matched no DML regex before."""
    body = (
        "def upgrade() -> None:\n"
        "    op.execute('UPDATE regulatory_pack' + \"ages SET return_code = 'X'\")\n"
    )

    assert _scan(body) == {"regulatory_packages"}


def test_a_helper_called_from_inside_the_block_counts_as_guarded() -> None:
    """Factoring the rewrite into a function is correct, not an evasion.

    `202608150013` is shaped exactly like this, and a purely lexical
    containment check convicted it. The region therefore follows calls to
    module-local functions.
    """
    body = (
        "from app.db.session import force_rls_suspended\n\n"
        "TABLES = ('regulatory_packages',)\n\n\n"
        "def _recode() -> None:\n"
        "    for table in TABLES:\n"
        "        op.execute(f\"UPDATE {table} SET return_code = 'X'\")\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), *TABLES):\n"
        "        _recode()\n"
    )

    assert _scan(body) == set()


def test_a_helper_also_called_from_open_code_is_still_convicted() -> None:
    """Following calls must not become a way to launder an unguarded write.

    A helper is treated as guarded only when EVERY call site is inside a block;
    one call from open code and the write is reported again.
    """
    body = (
        "from app.db.session import force_rls_suspended\n\n"
        "TABLES = ('regulatory_packages',)\n\n\n"
        "def _recode() -> None:\n"
        "    for table in TABLES:\n"
        "        op.execute(f\"UPDATE {table} SET return_code = 'X'\")\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), *TABLES):\n"
        "        _recode()\n"
        "    _recode()\n"
    )

    assert _scan(body) == {"regulatory_packages"}


def test_a_splatted_table_tuple_is_resolved_into_the_covered_set() -> None:
    """`GUARDED = ('a', *OTHERS)` is how `202608220029` composes its tuple.

    Dropping the splat made the spliced-in tables look uncovered, which is a
    false accusation — the failure mode that erodes trust in a guard fastest.
    """
    body = (
        "from app.db.session import force_rls_suspended\n\n"
        "OTHERS = ('return_signing_policies',)\n"
        "GUARDED = ('regulatory_packages', *OTHERS)\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), *GUARDED):\n"
        "        for table in GUARDED:\n"
        "            op.execute(f\"UPDATE {table} SET return_code = 'X'\")\n"
    )

    assert _scan(body) == set()


def test_an_unreadable_guard_argument_is_treated_as_covering_everything() -> None:
    """Conservative by choice: a scanner that cannot read the argument list
    must not invent an offence. The cost is a false negative, which is the
    right direction for a rule people have to trust."""
    body = (
        "from app.db.session import force_rls_suspended\n\n\n"
        "def upgrade() -> None:\n"
        "    with force_rls_suspended(op.get_bind(), *_tables_from_config()):\n"
        "        op.execute(\"UPDATE regulatory_packages SET return_code = 'X'\")\n"
    )

    assert _scan(body) == set()
