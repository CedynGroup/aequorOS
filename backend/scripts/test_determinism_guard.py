"""Ratchet unfrozen wall-clock reads out of tests; freeze the clock instead.

AQPY001 flags clock reads (``datetime.now/utcnow/today``, ``date.today``,
``time.time`` and the platform's ``utc_now`` helpers) anywhere under
``tests/`` — fixtures, ``tests/support`` and module level included — that are
not frozen. Test-execution reads are accepted under a ``freeze_time`` marker on
the function, class or module ``pytestmark``, inside
``with time_machine.travel(..., tick=False)`` (a ticking or ``None``-targeted
travel is not a deterministic instant), or in a function that takes the
``frozen_clock`` fixture. Import-time reads (module and class body statements,
default arguments) run before any travel starts, so a marker never covers them.
``time.monotonic`` and ``time.perf_counter`` measure elapsed time, not the
calendar, and are never flagged. A read carrying a valid
``# noqa: AQPY001 - <reason> (#<issue>)`` is excluded from the count. AQPY000
flags a ``# noqa: AQPY001`` or ``# noqa: TID251`` comment that lacks the
``- <reason> (#<issue>)`` tail.

The allowlist records counts by path and rule, not line numbers. New reads fail
immediately; removing reads requires shrinking the allowlist (stale counts fail
too), and ``--strict`` rejects even allowlisted ones.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import io
import re
import symtable
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import override

from pydantic import TypeAdapter

BACKEND = Path(__file__).resolve().parents[1]
ALLOWLIST = BACKEND / "tests/architecture/test_determinism_allowlist.json"

AQPY001 = "AQPY001"
AQPY000 = "AQPY000"
TID251 = "TID251"

#: Wall-clock readers. ``datetime.*`` appears in both import styles:
#: ``from datetime import datetime`` resolves ``datetime.now`` to
#: ``datetime.datetime.now``, while ``import datetime`` writes
#: ``datetime.datetime.now()`` spelled through the module.
CLOCK_MEMBERS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "date.today",
        "datetime.date.today",
        "time.time",
    }
)

#: The platform's own wall-clock helpers (app/db/base.py and
#: app/attestation/service/api.py); anything reading them inherits the same
#: flake risk as reading ``datetime.now`` directly.
UTC_NOW_SOURCES = frozenset(
    {
        "app.db.base.utc_now",
        "app.attestation.service.api.utc_now",
    }
)

FREEZE_MARKER = "pytest.mark.freeze_time"
TRAVEL = "time_machine.travel"
FIXTURE_DECORATORS = frozenset({"pytest.fixture", "pytest_asyncio.fixture"})
PARAMETRIZE = "pytest.mark.parametrize"
FROZEN_CLOCK = "frozen_clock"

#: Upper bound on the silent probe passes a loop runs while its carried
#: bindings settle; real chains stabilize in one or two rounds.
_PROBE_LIMIT = 8

#: A surviving suppression must name its reason and its tracking issue:
#: ``# noqa: AQPY001 - <reason> (#<issue>)``.
NOQA_CODES = re.compile(r"noqa:\s*(?P<codes>[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)")
SUPERSEDED = re.compile(
    r"noqa:\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*\s*-\s*(?P<reason>[^#]+?)\s*\(#(?P<issue>\d+)\)"
)
GUARDED_CODES = frozenset({AQPY001, TID251})


#: Separates the alternative bindings a conditional merge could not decide
#: between (``import time as clock`` vs ``import datetime as clock`` on
#: different branches). No module path contains ``|``.
AMBIGUOUS = "|"


def _alternatives(value: str) -> list[str]:
    return value.split(AMBIGUOUS) if AMBIGUOUS in value else [value]


def _clock_capable(value: str) -> bool:
    """Whether reads through a binding with this prefix can name a clock member."""
    return any(
        member == alternative or member.startswith(f"{alternative}.")
        for alternative in _alternatives(value)
        for member in (*CLOCK_MEMBERS, *UTC_NOW_SOURCES)
    )


def _descendants(table: symtable.SymbolTable) -> Iterator[symtable.SymbolTable]:
    """Every table in the subtree below ``table``, depth first."""
    for child in table.get_children():
        yield child
        yield from _descendants(child)


def _unique_reads(reads: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Drop repeated reads; the findings count one read per line and name."""
    unique: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for read in reads:
        if read not in seen:
            seen.add(read)
            unique.append(read)
    return unique


def _loop_has_break(statements: list[ast.stmt]) -> bool:
    """Whether a ``break`` at this loop's own level runs inside the body.

    A break inside a nested loop belongs to that loop, so the walk skips
    nested loop statements; a break inside a nested function is a syntax
    error, so statements are the only places to search.
    """
    for statement in statements:
        if isinstance(statement, ast.Break):
            return True
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.Break):
                return True
            if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                continue
            if isinstance(child, ast.stmt) and _loop_has_break([child]):
                return True
    return False


#: Prefix of the alias-map keys that record which generators a name may hold,
#: so the branch, loop and exception joins merge them with the bindings.
GENERATOR_KEY = "\x00generator:"
#: Prefix of the keys recording which function or lambda a name may hold, so a
#: reference to the name marks a point where its body may run.
CALLABLE_KEY = "\x00callable:"


def _merge_generators(values: list[str]) -> str:
    """Every generator a name may hold across the reachable paths."""
    held = dict.fromkeys(
        alternative for value in values for alternative in _alternatives(value) if alternative
    )
    return AMBIGUOUS.join(held)


def _merge_values(values: list[str]) -> str:
    """The binding a read may see across the reachable paths of a branch or loop.

    A path that may still hold a clock binding keeps it detectable: the merge
    keeps every clock-capable possibility until every reachable path replaces
    the binding, and drops the clock binding only when all paths agree.
    """
    if len(set(values)) == 1:
        return values[0]
    possibilities = [
        alternative
        for value in values
        for alternative in _alternatives(value)
        if _clock_capable(alternative)
    ]
    if possibilities:
        return AMBIGUOUS.join(dict.fromkeys(possibilities))
    return values[0]


#: An assignment expression inside a generator: the name it binds, its value
#: expression and the generator's own iteration targets, each as the binding
#: its first iterable supplied at creation or the later iterable to resolve at
#: consumption.
GeneratorBinding = tuple[str, ast.expr, list[tuple[ast.expr, dict[str, str] | ast.expr]]]


def _target_names(target: ast.expr) -> list[str]:
    """The names a loop or comprehension target binds."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _target_names(element)]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _aligned(
    targets: Sequence[ast.AST], values: Sequence[ast.expr]
) -> list[ast.expr | None] | None:
    """The value each target position receives, or ``None`` if unknowable.

    A starred target absorbs the surplus, so the fixed positions around it
    still pair with their values; the starred position itself has none.
    """
    if any(isinstance(value, ast.Starred) for value in values):
        return None
    starred = next(
        (
            index
            for index, target in enumerate(targets)
            if isinstance(target, (ast.Starred, ast.MatchStar))
        ),
        None,
    )
    if starred is None:
        return list(values) if len(values) == len(targets) else None
    if len(values) < len(targets) - 1:
        return None
    suffix = len(targets) - starred - 1
    return [*values[:starred], None, *values[len(values) - suffix :]]


def _destructure(
    target: ast.expr, elements: list[ast.expr], visitor: ClockVisitor
) -> dict[str, str]:
    """Bind ``target`` to the merge of ``elements``, position by position."""
    if isinstance(target, ast.Name):
        value = _merge_values([visitor.element_value(element) for element in elements])
        generators = _merge_generators(
            [visitor.captured_generators.get(id(element), "") for element in elements]
        )
        return {
            **({target.id: value} if value else {}),
            **({GENERATOR_KEY + target.id: generators} if generators else {}),
        }
    if not isinstance(target, (ast.Tuple, ast.List)):
        return {}
    alignments = [
        _aligned(target.elts, element.elts) if isinstance(element, (ast.Tuple, ast.List)) else None
        for element in elements
    ]
    if any(alignment is None for alignment in alignments):
        return {}
    result: dict[str, str] = {}
    for index, item in enumerate(target.elts):
        matched = [
            alignment[index]
            for alignment in alignments
            if alignment and alignment[index] is not None
        ]
        if matched:
            result.update(_destructure(item, [value for value in matched if value], visitor))
    return result


def _loop_bindings(target: ast.expr, iterable: ast.expr, visitor: ClockVisitor) -> dict[str, str]:
    """The bindings a loop or comprehension target takes from a literal iterable.

    A tuple or list literal assigns each element to the target in turn, so a
    known clock callable among them keeps the target resolvable, also through
    unpacking.
    """
    if isinstance(iterable, (ast.Tuple, ast.List)) and iterable.elts:
        return _destructure(target, list(iterable.elts), visitor)
    return {}


class ClockVisitor(ast.NodeVisitor):
    """Resolve clock-call bindings within each lexical scope in statement order.

    Mirrors the alias machinery of ``test_database_guard.py``: imports and
    assignments feed a per-scope alias map, ``symtable`` separates locals from
    globals, and function bodies are scanned after the module pass so calls are
    judged with the bindings visible at their use site.

    Two freeze states are tracked. ``frozen`` applies to statements that
    execute now (a ``time_machine.travel`` block entered in this scope);
    ``deferred_frozen`` applies to function bodies, which run later under a
    test's markers — a ``freeze_time`` marker or ``pytestmark`` contributes to
    the deferred state only, because import-time reads run before any travel
    starts.
    """

    def __init__(self, source: str, *, module_marker_applies: bool = True) -> None:
        #: Whether a module-level ``pytestmark`` reaches the tests: pytest
        #: never propagates a ``conftest.py`` module's marks to the items
        #: that use its fixtures.
        self.module_marker_applies = module_marker_applies
        self.table: symtable.SymbolTable = symtable.symtable(source, "<test>", "exec")
        self.aliases: dict[str, str] = {}
        self.enclosing = self.aliases
        self.module_aliases = self.aliases
        self.reads: list[tuple[str, int]] = []
        self.children: dict[symtable.SymbolTable, list[symtable.SymbolTable]] = {}
        # Symbol tables already handed out, kept alive by reference: two
        # definitions can share a line (lambdas in one annotation) and a match
        # must not hand out twice, and ids of collected tables would be reused.
        self.consumed: dict[int, symtable.SymbolTable] = {}
        #: Tables assigned to dictionary subexpressions before the visit, in
        #: the symbol table's key-before-value order.
        self.preassigned: dict[int, symtable.SymbolTable] = {}
        #: Snapshots of the bindings at each reachable ``break``, one list
        #: per enclosing loop, innermost last.
        self.break_states: list[list[dict[str, str]]] = []
        #: Snapshots of the bindings at each reachable ``continue``, one
        #: list per enclosing loop, innermost last.
        self.continue_states: list[list[dict[str, str]]] = []
        #: Snapshots of the bindings at each reachable ``return`` of the
        #: visited function body.
        self.return_states: list[dict[str, str]] = []
        self.probing = False
        #: Snapshots of the bindings after each statement of the ``try``
        #: being visited, at any nesting depth: an exception may leave the
        #: statement list at any of them.
        self.exception_snapshots: list[dict[str, str]] | None = None
        #: The binding a generator's first iterable supplies, captured when
        #: the generator is created: that iterable evaluates eagerly.
        self.first_iterable_values: dict[int, dict[str, str]] = {}
        #: The generator that is the value of the assignment being visited.
        self.assigned_generator: int | None = None
        #: The bindings that generator's assignment expressions may make.
        self.generator_bindings: list[GeneratorBinding] = []
        #: Per generator, the bindings consuming it may make.
        self.generator_effects: dict[int, list[GeneratorBinding]] = {}
        #: The enclosing bindings each generator's body is scanned under: the
        #: bindings at creation until the generator is consumed, then those
        #: at its consumption.
        self.generator_scopes: dict[int, dict[str, str]] = {}
        #: The generators whose consumption has set their scan bindings.
        self.consumed_generators: set[int] = set()
        #: Whether each generator's body runs under a freeze: the freeze
        #: active where it is consumed.
        self.generator_frozen: dict[int, bool] = {}
        #: The bindings each nested function or lambda is scanned under: the
        #: merge of the states at the points where it is referenced.
        self.callable_scopes: dict[int, dict[str, str]] = {}
        #: Whether the visited statements run inside a function body, where
        #: nothing is collected or registered by pytest.
        self.in_function_body = False
        #: Whether pytest collects tests from the visited scope: the module,
        #: and the classes named ``Test*`` inside it.
        self.collectable_scope = True
        #: Whether an enclosing class or module parametrizes ``frozen_clock``
        #: directly for the tests inside it.
        self.inherited_parametrization = False
        #: What each assignment expression evaluated to, before its target
        #: was bound.
        self.walrus_results: dict[int, str] = {}
        #: The generators among those elements, by element.
        self.captured_generators: dict[int, str] = {}
        #: Bindings of the elements of the unpacked value being evaluated,
        #: captured as each element evaluates.
        self.captured_elements: dict[int, str] = {}
        #: Whether the visited statements run inside a deferred function or
        # lambda body: Python evaluates function-local variable annotations
        # never, while module and class bodies evaluate theirs at execution.
        self.deferred_body = False
        #: Names bound by assignment expressions in the currently visited
        # comprehension; the comprehension hands them to its containing scope.
        self.walrus: set[str] = set()
        self.frozen = False
        self.deferred_frozen = False
        self.postponed = False
        #: Clock bindings each function or lambda captured through its
        #: parameter defaults, keyed by node id.
        self.default_bindings: dict[int, dict[str, str]] = {}
        self.functions: list[
            tuple[
                ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp,
                symtable.SymbolTable,
                dict[str, str],
                bool,
            ]
        ] = []

    def qualified(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, "")
        if isinstance(node, ast.Attribute):
            # Qualify each conditional alternative separately: appending the
            # attribute to the merged string would lose the member names.
            return AMBIGUOUS.join(
                f"{alternative}.{node.attr}"
                for alternative in _alternatives(self.qualified(node.value))
            )
        if isinstance(node, ast.IfExp):
            # A conditional expression selects its callable the same way an if
            # statement rebinds a name: keep every clock-capable alternative
            # so neither path escapes the guard.
            return _merge_values([self.qualified(node.body), self.qualified(node.orelse)])
        if isinstance(node, ast.BoolOp):
            # A boolean expression yields one of its operands: a fallback such
            # as ``fake_clock or datetime.now`` may still read the wall clock.
            return _merge_values([self.qualified(operand) for operand in node.values])
        if isinstance(node, ast.NamedExpr):
            # An assignment expression yields the value it evaluated, even if
            # its target replaced the alias that value was read through.
            result = self.walrus_results.get(id(node))
            return result if result is not None else self.qualified(node.value)
        return ""

    def possible_names(self, node: ast.expr) -> Iterator[str]:
        """Every dotted name a call through ``node`` may resolve to.

        A conditionally rebound alias carries ``AMBIGUOUS``-separated
        possibilities; a call is a clock read when any of them names a clock
        member, because any path may hold that binding at the call.
        """
        if isinstance(node, ast.Name):
            yield from _alternatives(self.aliases.get(node.id, ""))
        elif isinstance(node, ast.Attribute):
            for base in self.possible_names(node.value):
                yield f"{base}.{node.attr}"
        elif isinstance(node, ast.IfExp):
            # A call through a conditional expression may select either
            # branch's callable.
            yield from self.possible_names(node.body)
            yield from self.possible_names(node.orelse)
        elif isinstance(node, ast.BoolOp):
            # A call through a boolean expression may select any operand's
            # callable, such as ``(time.time or None)()``.
            for operand in node.values:
                yield from self.possible_names(operand)
        elif isinstance(node, ast.NamedExpr):
            result = self.walrus_results.get(id(node))
            if result is None:
                yield from self.possible_names(node.value)
            else:
                yield from _alternatives(result)
        else:
            yield ""

    def child_table(self, node: ast.expr | ast.stmt, name: str) -> symtable.SymbolTable:
        children = self.children.setdefault(self.table, self.table.get_children())
        index = next(
            (
                index
                for index, child in enumerate(children)
                if id(child) not in self.consumed
                and child.get_name() == name
                and child.get_lineno() == node.lineno
            ),
            None,
        )
        if index is None:
            # A definition inside a wrapper scope (PEP 695 type parameters, a
            # type alias) is not a direct child of the visiting scope; search
            # the nested tables for it.
            child = next(
                nested
                for wrapper in children
                for nested in _descendants(wrapper)
                if id(nested) not in self.consumed
                and nested.get_name() == name
                and nested.get_lineno() == node.lineno
            )
        else:
            child = children.pop(index)
        self.consumed[id(child)] = child
        if child.get_type() not in {"function", "class"}:
            child = next(
                nested
                for nested in child.get_children()
                if id(nested) not in self.consumed
                and nested.get_name() == name
                and nested.get_lineno() == node.lineno
            )
            self.consumed[id(child)] = child
        return child

    def visit_Call(self, node: ast.Call) -> None:
        # The callable resolves after its own expression evaluated, and before
        # the arguments can rebind anything.
        self.visit(node.func)
        for name in self.possible_names(node.func):
            if (name in CLOCK_MEMBERS or name in UTC_NOW_SOURCES) and not self.frozen:
                self.reads.append((name, node.lineno))
                break
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = (
                f"{node.module}.{alias.name}" if node.level == 0 else ""
            )

    def bindings(self, target: ast.expr, value: ast.expr | None = None) -> dict[str, str]:
        if isinstance(target, ast.Name):
            # Rebinding the name discards the generator it held.
            if value is None:
                return {target.id: "", GENERATOR_KEY + target.id: "", CALLABLE_KEY + target.id: ""}
            captured = self.captured_elements.get(id(value))
            return {
                target.id: captured if captured is not None else self.qualified(value),
                GENERATOR_KEY + target.id: self.captured_generators.get(id(value), ""),
                CALLABLE_KEY + target.id: "",
            }
        if isinstance(target, (ast.Tuple, ast.List)):
            aligned = (
                _aligned(target.elts, value.elts)
                if isinstance(value, (ast.Tuple, ast.List))
                else None
            )
            result: dict[str, str] = {}
            for index, item in enumerate(target.elts):
                result.update(self.bindings(item, aligned[index] if aligned else None))
            return result
        if isinstance(target, ast.Starred):
            return self.bindings(target.value)
        self.visit(target)
        return {}

    def bind(self, target: ast.expr, value: ast.expr | None = None) -> None:
        self.aliases.update(self.bindings(target, value))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.aliases[node.id] = ""
            self.aliases[GENERATOR_KEY + node.id] = ""
            self.aliases[CALLABLE_KEY + node.id] = ""
            return
        for held in _alternatives(self.aliases.get(CALLABLE_KEY + node.id, "")):
            if held:
                self._reference_callable(int(held))
        for held in _alternatives(self.aliases.get(GENERATOR_KEY + node.id, "")):
            if held:
                generator = int(held)
                self._apply_generator_bindings(self.generator_effects.get(generator, []))
                self._consume_generator(generator)

    def _reference_callable(self, callable_id: int) -> None:
        """Note a point where a nested function's body may run."""
        known = self.callable_scopes.get(callable_id)
        current = dict(self.aliases)
        self.callable_scopes[callable_id] = self._merged([known, current] if known else [current])

    def _consume_generator(self, generator: int) -> None:
        """Scan a generator's body under the bindings at its consumption."""
        current = dict(self.aliases)
        known = self.generator_scopes.get(generator)
        frozen = self.frozen
        if known is None or generator not in self.consumed_generators:
            self.generator_scopes[generator] = current
            self.generator_frozen[generator] = frozen
        else:
            self.generator_frozen[generator] = (
                self.generator_frozen.get(generator, False) and frozen
            )
            self.generator_scopes[generator] = {
                name: _merge_values([known.get(name, ""), current.get(name, "")])
                for name in (*known, *current)
            }
        self.consumed_generators.add(generator)

    def _apply_generator_bindings(self, bindings: list[GeneratorBinding]) -> None:
        """Merge the bindings a consumed generator may have made.

        The assigned values resolve under the bindings at consumption, with
        the generator's own iteration targets holding what they held when it
        was created.
        """
        for name, value, supplied in bindings:
            # Each level's targets are installed before the next iterable
            # resolves, because a later iterable may name an earlier target.
            previous: dict[str, str | None] = {}
            for target, source in supplied:
                bound = source if isinstance(source, dict) else _loop_bindings(target, source, self)
                for target_name in _target_names(target):
                    previous.setdefault(target_name, self.aliases.get(target_name))
                    self.aliases[target_name] = bound.get(target_name, "")
            resolved = self.qualified(value)
            for target_name, earlier in previous.items():
                if earlier is None:
                    self.aliases.pop(target_name, None)
                else:
                    self.aliases[target_name] = earlier
            self.aliases[name] = _merge_values([self.aliases.get(name, ""), resolved])

    def _visit_assigned_value(self, value: ast.expr) -> None:
        """Visit an assignment's value; a generator waits for its consumer."""
        if isinstance(value, ast.GeneratorExp):
            self.assigned_generator = id(value)
            self.generator_bindings = []
        self.visit(value)
        self.assigned_generator = None

    def element_value(self, element: ast.expr) -> str:
        """The binding an element of a literal sequence evaluated to."""
        captured = self.captured_elements.get(id(element))
        return captured if captured is not None else self.qualified(element)

    def _visit_iterable(self, iterable: ast.expr) -> None:
        """Evaluate an iterable, capturing a literal's elements as they evaluate."""
        if isinstance(iterable, (ast.Tuple, ast.List)):
            self._evaluate_captured(iterable)
        else:
            self.visit(iterable)

    def _evaluate_captured_value(self, value: ast.expr) -> None:
        """Evaluate a dictionary value, capturing it and any literal elements."""
        if isinstance(value, (ast.Tuple, ast.List)):
            self._evaluate_captured(value)
        else:
            self.visit(value)
        self.captured_elements[id(value)] = self.qualified(value)

    def _evaluate_captured(self, value: ast.expr) -> None:
        """Evaluate an unpacked value, capturing each element as it evaluates."""
        if isinstance(value, (ast.Tuple, ast.List)):
            for element in value.elts:
                self._evaluate_captured(element)
            return
        held = self._held_generator(value)
        if isinstance(value, ast.GeneratorExp):
            # A generator inside the unpacked value waits for the consumer of
            # the target that receives it.
            self._visit_assigned_value(value)
            self.generator_effects[id(value)] = self.generator_bindings
            self.captured_generators[id(value)] = str(id(value))
        elif held is not None:
            # So does one that an existing variable already holds.
            self.captured_generators[id(value)] = held
        else:
            self.visit(value)
        self.captured_elements[id(value)] = self.qualified(value)

    def _held_generator(self, value: ast.expr) -> str | None:
        """The generators a plain variable copy passes on unconsumed."""
        if isinstance(value, ast.Name):
            return self.aliases.get(GENERATOR_KEY + value.id) or None
        return None

    def _hold_generator(self, target: ast.expr, value: ast.expr, held: str | None = None) -> None:
        """Keep a generator's effects for where its variable is consumed.

        A lambda bound to a name is held the same way, so its references mark
        where its body may run.
        """
        if not isinstance(target, ast.Name):
            return
        if isinstance(value, ast.Lambda):
            self.aliases[CALLABLE_KEY + target.id] = str(id(value))
        if held is not None:
            self.aliases[GENERATOR_KEY + target.id] = held
        elif isinstance(value, ast.GeneratorExp):
            self.generator_effects[id(value)] = self.generator_bindings
            self.aliases[GENERATOR_KEY + target.id] = str(id(value))

    def _preassign_targets_before(self, targets: list[ast.expr], *later: ast.expr | None) -> None:
        """Reserve target scopes ahead of the expressions that follow them.

        The symbol table lists a statement's target scopes before its value's,
        while the value evaluates first.
        """
        if self.probing:
            return
        for expression in (*targets, *later):
            if expression is not None:
                self._preassign_expression_tables(expression)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._preassign_targets_before(node.targets, node.value)
        held = self._held_generator(node.value)
        if isinstance(node.value, (ast.Tuple, ast.List)):
            self._evaluate_captured(node.value)
        elif held is None:
            self._visit_assigned_value(node.value)
        name = self.qualified(node.value)
        for target in node.targets:
            self.bind(target, node.value)
            if isinstance(target, ast.Name):
                self.aliases[target.id] = name
            self._hold_generator(target, node.value, held)
        self.captured_elements.clear()
        self.captured_generators.clear()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # The symbol table lists the target's scopes, then the annotation's,
        # then the value's; a postponed or function-local annotation has no
        # scope at all.
        annotation = node.annotation if not self.postponed and not self.deferred_body else None
        self._preassign_targets_before([node.target], annotation, node.value)
        # Python assigns the value before evaluating the annotation, so the
        # annotation's bindings cannot mask the value's own reads.
        if node.value is not None:
            held = self._held_generator(node.value)
            if held is None:
                self._visit_assigned_value(node.value)
            self.bind(node.target, node.value)
            self._hold_generator(node.target, node.value, held)
        if not self.postponed and not self.deferred_body:
            # Module and class bodies evaluate their annotations; a
            # function-local one never runs.
            self.visit(node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._preassign_targets_before([node.target], node.value)
        if isinstance(node.target, ast.Name):
            self.visit(node.value)
            self.bind(node.target)
        else:
            # The target's subexpressions evaluate before the value.
            self.visit(node.target)
            self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.walrus_results[id(node)] = self.qualified(node.value)
        self.bind(node.target, node.value)
        if self.exception_snapshots is not None and not self.probing:
            # An exception may surface right after the binding, before the
            # statement that holds the expression completes.
            self.exception_snapshots.append(dict(self.aliases))
        # An assignment expression binds in the containing scope even inside
        # a comprehension; the comprehension hands the record on.
        self.walrus.add(node.target.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name is not None:
            self.aliases[node.name] = ""
        for statement in node.body:
            self.visit(statement)
        if node.name is not None:
            self.aliases[node.name] = ""

    @staticmethod
    def _merged(states: list[dict[str, str]]) -> dict[str, str]:
        """The union of the bindings the reachable ``states`` may hold."""
        keys: set[str] = set()
        for state in states:
            keys.update(state)
        return {
            key: (
                _merge_generators
                if key.startswith((GENERATOR_KEY, CALLABLE_KEY))
                else _merge_values
            )([state.get(key, "") for state in states])
            for key in keys
        }

    def _merge_states(self, states: list[dict[str, str]]) -> None:
        """Rebuild ``self.aliases`` as the union of the reachable path states.

        The mutation is in place: ``module_aliases`` and ``enclosing`` share
        this dict, so a rebound map would leave them seeing the unmerged state.
        """
        merged = self._merged(states)
        self.aliases.clear()
        self.aliases.update(merged)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = dict(self.aliases)
        for statement in node.body:
            self.visit(statement)
        after_body = dict(self.aliases)
        self.aliases.clear()
        self.aliases.update(before)
        for statement in node.orelse:
            self.visit(statement)
        # Without an ``else`` the fall-through path keeps the pre-branch
        # bindings; with one, only the two branch states are reachable.
        self._merge_states([after_body, dict(self.aliases) if node.orelse else before])

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        if not self.probing:
            # The symbol table lists the target's scopes before the
            # iterable's, while the iterable evaluates first.
            self._preassign_expression_tables(node.target)
            self._preassign_expression_tables(node.iter)
        self._visit_iterable(node.iter)
        # Zero iterations leave the target holding its incoming value.
        pre_target = dict(self.aliases)
        target_bindings = _loop_bindings(node.target, node.iter, self)
        self._bind_targets_silently([(node.target, target_bindings)])
        self._visit_loop(
            node, pre_target=pre_target, target=node.target, target_bindings=target_bindings
        )

    visit_AsyncFor = visit_For

    def _bind_loop_target(self, target: ast.expr, bindings: dict[str, str]) -> None:
        """Bind a loop target, keeping known clock callables resolvable."""
        self.bind(target)
        self.aliases.update(bindings)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_loop(node, node.test)

    def _visit_loop(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        recheck: ast.expr | None = None,
        pre_target: dict[str, str] | None = None,
        target: ast.expr | None = None,
        target_bindings: dict[str, str] | None = None,
    ) -> None:
        # A loop body may run zero times, so its assignments must not clear a
        # binding: merge the pre-loop state with the body and ``else`` states.
        # A body assignment also carries into later iterations, so a read
        # before the assignment is judged with the binding any iteration may
        # hold: a silent pass collects the body's assignments first.
        before = dict(self.aliases)
        probe_start = len(self.reads)
        outer_probing = self.probing
        self.probing = True
        self.break_states.append([])
        self.continue_states.append([])
        try:
            # Assignment chains settle across iterations, so the silent pass
            # repeats until the carried bindings stabilize. Each iteration
            # reassigns the target from the iterable first.
            for _ in range(_PROBE_LIMIT):
                start_state = dict(self.aliases)
                if target is not None:
                    self._bind_loop_target(target, target_bindings or {})
                for statement in node.body:
                    self.visit(statement)
                del self.reads[probe_start:]
                self._merge_states([before, dict(self.aliases), *self.continue_states[-1]])
                if self.aliases == start_state:
                    break
        finally:
            self.probing = outer_probing
            self.break_states.pop()
            continue_states = self.continue_states.pop()
        if recheck is not None:
            # A while condition is re-evaluated every iteration, so the
            # evaluations after the first run under the carried bindings. The
            # silent mode leaves the already-recorded definitions and their
            # tables alone.
            outer_probing = self.probing
            self.probing = True
            try:
                self.visit(recheck)
            finally:
                self.probing = outer_probing
        carried = dict(self.aliases)
        # Each iteration reassigns the target from the iterable before the
        # body runs.
        if target is not None:
            self._bind_loop_target(target, target_bindings or {})
        self.break_states.append([])
        self.continue_states.append([])
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            break_states = self.break_states.pop()
            real_continue_states = self.continue_states.pop()
        after_visit = dict(self.aliases)
        # A loop that runs zero iterations exits with the pre-target state,
        # and a continue hands its bindings to the next iteration and thus
        # to every later exit.
        entry = [carried] if pre_target is None else [carried, pre_target]
        entry = [*entry, *continue_states, *real_continue_states]
        self._merge_loop_exit(node, entry, after_visit, break_states)

    def _merge_loop_exit(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        entry: list[dict[str, str]],
        after_visit: dict[str, str],
        break_states: list[dict[str, str]],
    ) -> None:
        """Join the loop's exit states into ``self.aliases``."""
        if _loop_has_break(node.body):
            # A break jumps past the else block, so only the break-time
            # states and the completed exit stay reachable; the else block
            # itself still sees the entry states when the loop completes.
            exits = break_states or [after_visit]
            if node.orelse:
                self.aliases.clear()
                self._merge_states(entry)
                for statement in node.orelse:
                    self.visit(statement)
                self._merge_states([*exits, dict(self.aliases)])
            else:
                self._merge_states([*exits, *entry])
        elif node.orelse:
            # Without a break the loop always completes, so the else block
            # runs and its state is the only exit state.
            self.aliases.clear()
            self._merge_states(entry)
            for statement in node.orelse:
                self.visit(statement)
        else:
            self.aliases.clear()
            self._merge_states([*entry, after_visit])

    def visit_Break(self, node: ast.Break) -> None:
        # A break exits its innermost loop with the bindings at that point;
        # the silent passes re-visit the same breaks, so only the reporting
        # pass records them.
        if not self.probing and self.break_states:
            self.break_states[-1].append(dict(self.aliases))

    def visit_Continue(self, node: ast.Continue) -> None:
        # A continue hands the current bindings to the next iteration; the
        # silent passes record them for the loop-carried merge.
        if self.continue_states:
            self.continue_states[-1].append(dict(self.aliases))

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            self.visit(node.value)
        # The silent passes re-visit the same returns, so only the reporting
        # pass records them.
        if not self.probing:
            self.return_states.append(dict(self.aliases))

    def _jump_stacks(self) -> list[list[dict[str, str]]]:
        """The state lists a jump out of the visited ``try`` appends to."""
        return [
            self.break_states[-1] if self.break_states else [],
            self.continue_states[-1] if self.continue_states else [],
            self.return_states,
        ]

    @override
    def visit(self, node: ast.AST) -> None:
        super().visit(node)
        if isinstance(node, ast.stmt) and self.exception_snapshots is not None and not self.probing:
            self.exception_snapshots.append(dict(self.aliases))

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:
        before = dict(self.aliases)
        outer_snapshots = self.exception_snapshots
        # An exception that no handler stops, or one raised inside a handler,
        # still runs the finally block under the bindings at its raise point.
        exception_states = [before]
        self.exception_snapshots = exception_states
        # Only jumps recorded from here on cross this try's finally.
        marks = [len(stack) for stack in self._jump_stacks()]
        for statement in node.body:
            self.visit(statement)
        after_body = dict(self.aliases)
        # An exception may surface at any point of the body, at any nesting
        # depth, so the handlers start from the state after each statement.
        body_states = list(exception_states)
        states: list[dict[str, str]] = []
        for handler in node.handlers:
            self.aliases.clear()
            self._merge_states(body_states)
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name is not None:
                # The handler body sees the exception object, and Python
                # deletes the name when the block exits.
                self.aliases[handler.name] = ""
            for statement in handler.body:
                self.visit(statement)
            if handler.name is not None:
                self.aliases[handler.name] = ""
            states.append(dict(self.aliases))
        if node.orelse:
            # Successful execution always passes through the else block, so
            # its state is the only successful exit state.
            self.aliases.clear()
            self.aliases.update(after_body)
            for statement in node.orelse:
                self.visit(statement)
            states.append(dict(self.aliases))
        else:
            states.append(after_body)
        self._merge_states(states)
        self.exception_snapshots = outer_snapshots
        for statement in node.finalbody:
            self.visit(statement)
        escaping = self._apply_finally_to_jumps(node, marks, exception_states)
        if outer_snapshots is not None:
            # A body exception reaches the enclosing statement unless a
            # catch-all handler stops it, and the handler and else states
            # always do, each after this finally.
            reaching = escaping[len(body_states) :] if _catches_everything(node) else escaping
            outer_snapshots.extend(reaching)

    def _apply_finally_to_jumps(
        self,
        node: ast.Try | ast.TryStar,
        marks: list[int],
        exception_states: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Fold the finally block's assignments into pending jump states.

        The exception states are returned with the finally block applied, for
        an enclosing statement an escaping exception reaches next.
        """
        if not node.finalbody:
            return exception_states
        stacks = self._jump_stacks()
        pending = [stack[mark:] for stack, mark in zip(stacks, marks, strict=True)]
        # A break, continue or return crosses the finally block, so each
        # pending jump state must include the finally's assignments. The
        # re-visits run silently to spare the consumed tables; a read that
        # only the jump path reaches is new evidence and stays, while reads
        # the normal pass already recorded are dropped as duplicates.
        normal_state = dict(self.aliases)
        known_reads = set(self.reads)
        read_mark = len(self.reads)
        outer_probing = self.probing
        self.probing = True
        transformed: list[list[dict[str, str]]] = [[] for _ in pending]
        escaping: list[dict[str, str]] = []
        try:
            for states, folded in zip(pending, transformed, strict=True):
                for state in states:
                    self.aliases.clear()
                    self.aliases.update(state)
                    for statement in node.finalbody:
                        self.visit(statement)
                    folded.append(dict(self.aliases))
            for state in exception_states:
                self.aliases.clear()
                self.aliases.update(state)
                for statement in node.finalbody:
                    self.visit(statement)
                escaping.append(dict(self.aliases))
        finally:
            self.probing = outer_probing
            fresh = [read for read in self.reads[read_mark:] if read not in known_reads]
            del self.reads[read_mark:]
            self.reads.extend(dict.fromkeys(fresh))
            self.aliases.clear()
            self.aliases.update(normal_state)
        for stack, mark, folded in zip(stacks, marks, transformed, strict=True):
            stack[mark:] = folded
        return escaping

    visit_TryStar = visit_Try

    def visit_Dict(self, node: ast.Dict) -> None:
        if not self.probing:
            # Python's symbol table lists dictionary-key scopes before value
            # scopes, while the entries evaluate interleaved; assign each
            # entry's tables in symbol-table order before the visit.
            for key in node.keys:
                if key is not None:
                    self._preassign_expression_tables(key)
            for value in node.values:
                self._preassign_expression_tables(value)
        # Each entry evaluates its key before its value, so a later entry's
        # bindings cannot mask an earlier entry's reads.
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
                self.visit(key)
            self._evaluate_captured_value(value)

    def _scan_comprehension_pass(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        level: int,
    ) -> list[dict[str, str]]:
        """Scan one comprehension iteration from ``level`` down.

        Deeper generator levels repeat within an outer iteration without
        rerunning the outer filters, so each level settles the levels below
        it; a pass whose entry state equals the previous pass's entry state
        only repeats the tail that pass already recorded. The innermost
        level's own repetition belongs to its caller, because each of its
        iterations reruns its target and filters.
        """
        rejected: list[dict[str, str]] = []
        generator = node.generators[level]
        if level:
            self._visit_iterable(generator.iter)
        captured = self.first_iterable_values.get(id(node)) if level == 0 else None
        self._bind_loop_target(
            generator.target,
            captured
            if captured is not None
            else _loop_bindings(generator.target, generator.iter, self),
        )
        for condition in generator.ifs:
            self.visit(condition)
            # A false filter rejects the iteration, so its post-condition
            # state stays reachable.
            rejected.append(dict(self.aliases))
        deeper = level + 1
        if deeper == len(node.generators):
            for expression in (
                [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            ):
                self.visit(expression)
            return rejected
        previous_entry: dict[str, str] | None = None
        scan_start = len(self.reads)
        for _ in range(_PROBE_LIMIT):
            entry_state = dict(self.aliases)
            outer_probing = self.probing
            # Later passes re-visit definitions whose tables are already
            # consumed, so they scan silently; an inherited silent pass
            # stays silent throughout.
            self.probing = outer_probing or previous_entry is not None
            try:
                rejected.extend(self._scan_comprehension_pass(node, deeper))
            finally:
                self.probing = outer_probing
            if entry_state == previous_entry:
                del self.reads[scan_start:]
                break
            previous_entry = entry_state
            scan_start = len(self.reads)
        return rejected

    def _preassign_expression_tables(self, expression: ast.expr) -> None:
        """Consume an expression's tables in the symbol table's order."""
        if isinstance(expression, ast.Lambda):
            if id(expression) not in self.preassigned:
                # A default's table precedes the lambda's own in the
                # enclosing scope, because defaults evaluate first.
                for default in (*expression.args.defaults, *expression.args.kw_defaults):
                    if default is not None:
                        self._preassign_expression_tables(default)
                self.preassigned[id(expression)] = self.child_table(expression, "lambda")
            # The body's own tables belong to the lambda's scope.
            return
        if isinstance(expression, ast.GeneratorExp):
            self._preassign_generator_tables(expression)
            return
        if isinstance(expression, (ast.ListComp, ast.SetComp, ast.DictComp)):
            self._preassign_eager_comprehension_tables(expression)
            return
        if isinstance(expression, ast.Dict):
            self._preassign_dict_tables(expression)
            return
        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.keyword):
                self._preassign_expression_tables(child.value)
            elif isinstance(child, ast.expr):
                self._preassign_expression_tables(child)

    def _preassign_dict_tables(self, expression: ast.Dict) -> None:
        """Assign a dictionary literal's tables, keys before values."""
        for key in expression.keys:
            if key is not None:
                self._preassign_expression_tables(key)
        for value in expression.values:
            self._preassign_expression_tables(value)

    def _preassign_generator_tables(self, expression: ast.GeneratorExp) -> None:
        """Assign a generator expression's tables in symbol-table order."""
        if id(expression) not in self.preassigned:
            # The first iterable evaluates in the enclosing scope, so its
            # tables precede the generator's own.
            self._preassign_expression_tables(expression.generators[0].iter)
            self.preassigned[id(expression)] = self.child_table(expression, "genexpr")

    def _preassign_generator_levels(
        self,
        expression: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        *,
        first_iterable: bool,
    ) -> None:
        """Assign each generator level's tables in symbol-table order.

        A level lists its target, then its iterable (the first level's
        iterable evaluates in the enclosing scope), then its filters.
        """
        for index, generator in enumerate(expression.generators):
            if index == 0 and first_iterable:
                self._preassign_expression_tables(generator.iter)
            self._preassign_expression_tables(generator.target)
            if index:
                self._preassign_expression_tables(generator.iter)
            for condition in generator.ifs:
                self._preassign_expression_tables(condition)

    def _preassign_eager_comprehension_tables(
        self, expression: ast.ListComp | ast.SetComp | ast.DictComp
    ) -> None:
        """Assign an eager comprehension's tables in symbol-table order."""
        # An eager comprehension inlines into the enclosing scope, and the
        # symbol table lists the generator levels first.
        self._preassign_generator_levels(expression, first_iterable=True)
        if isinstance(expression, ast.DictComp):
            # The symbol table lists a dictionary comprehension's value
            # scope before its key scope.
            self._preassign_expression_tables(expression.value)
            self._preassign_expression_tables(expression.key)
        else:
            self._preassign_expression_tables(expression.elt)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.visit(node.left)
        # Every comparison after the first may be short-circuited away, so
        # each evaluated prefix's bindings stay reachable at the join.
        states: list[dict[str, str]] = []
        for comparator in node.comparators:
            self.visit(comparator)
            states.append(dict(self.aliases))
        self._merge_states(states)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # The first operand always evaluates; later ones may be skipped, so
        # every evaluated prefix's bindings stay reachable at the join.
        states: list[dict[str, str]] = []
        for operand in node.values:
            self.visit(operand)
            states.append(dict(self.aliases))
        self._merge_states(states)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        before = dict(self.aliases)
        self.visit(node.body)
        after_body = dict(self.aliases)
        self.aliases.clear()
        self.aliases.update(before)
        self.visit(node.orelse)
        # Exactly one branch runs, like an if statement with an else: the
        # pre-expression binding survives only through the branch that
        # leaves it untouched.
        self._merge_states([after_body, dict(self.aliases)])

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_iterable(node.subject)
        before = dict(self.aliases)
        subject_value = self.qualified(node.subject)
        states: list[dict[str, str]] = []
        guard_states: list[dict[str, str]] = []
        exhaustive = False
        for case in node.cases:
            self.aliases.clear()
            # A failed guard's bindings carry into the later cases, because
            # the guard expression ran before rejecting its case.
            self._merge_states([before, *guard_states])
            # A pattern captures the matched value before the guard or the
            # case body runs; a bare capture holds the subject itself, while
            # any other capture holds an unknown value.
            self._bind_pattern_captures(case.pattern, subject_value, node.subject)
            if case.guard is not None:
                self.visit(case.guard)
                guard_states.append(dict(self.aliases))
            for statement in case.body:
                self.visit(statement)
            states.append(dict(self.aliases))
            if (
                isinstance(case.pattern, ast.MatchAs)
                and case.pattern.pattern is None
                and case.guard is None
            ):
                exhaustive = True
        # A match may fall through every case, so the pre-match bindings
        # stay reachable unless a wildcard case covers the subject; the
        # failed guards' bindings persist either way.
        if not exhaustive:
            states.append(before)
            states.extend(guard_states)
        self._merge_states(states)

    def _bind_sequence_captures(self, pattern: ast.MatchSequence, subject: ast.expr | None) -> None:
        """Bind each capture of a sequence pattern to its matched element."""
        elements = (
            _aligned(pattern.patterns, subject.elts)
            if isinstance(subject, (ast.Tuple, ast.List))
            else None
        )
        for index, item in enumerate(pattern.patterns):
            element = elements[index] if elements is not None else None
            self._bind_pattern_captures(
                item, self.element_value(element) if element is not None else "", element
            )

    def _bind_mapping_captures(self, pattern: ast.MatchMapping, subject: ast.expr | None) -> None:
        """Bind each capture of a mapping pattern to its literal subject value."""
        if pattern.rest is not None:
            self.aliases[pattern.rest] = ""
        for key, item in zip(pattern.keys, pattern.patterns, strict=True):
            value = None
            if isinstance(subject, ast.Dict) and isinstance(key, ast.Constant):
                value = next(
                    (
                        entry
                        for literal, entry in zip(subject.keys, subject.values, strict=True)
                        if isinstance(literal, ast.Constant) and literal.value == key.value
                    ),
                    None,
                )
            self._bind_pattern_captures(
                item, self.element_value(value) if value is not None else "", value
            )

    def _bind_pattern_captures(
        self, pattern: ast.pattern, subject_value: str, subject: ast.expr | None
    ) -> None:
        """Apply a case pattern's capture bindings against ``self.aliases``.

        A sequence pattern matches its subject element by element, so each
        nested capture holds the binding of its own element.
        """
        if isinstance(pattern, ast.MatchAs):
            if pattern.name is not None:
                # A capture, bare or with ``as``, holds the subject itself,
                # including a generator it is.
                self.aliases[pattern.name] = subject_value
                self.aliases[GENERATOR_KEY + pattern.name] = (
                    self.captured_generators.get(id(subject), "") if subject is not None else ""
                )
            if pattern.pattern is not None:
                self._bind_pattern_captures(pattern.pattern, subject_value, subject)
        elif isinstance(pattern, ast.MatchSequence):
            self._bind_sequence_captures(pattern, subject)
        elif isinstance(pattern, ast.MatchMapping):
            self._bind_mapping_captures(pattern, subject)
        elif isinstance(pattern, ast.MatchOr):
            # Any alternative may be the one that matched, so each binds from
            # the incoming state and the results merge.
            incoming = dict(self.aliases)
            alternatives: list[dict[str, str]] = []
            for alternative in pattern.patterns:
                self.aliases.clear()
                self.aliases.update(incoming)
                self._bind_pattern_captures(alternative, subject_value, subject)
                alternatives.append(dict(self.aliases))
            self._merge_states(alternatives)
        else:
            self._bind_opaque_captures(pattern)

    def _bind_opaque_captures(self, pattern: ast.pattern) -> None:
        """Bind the captures of a pattern whose matched value is unknown."""
        if isinstance(pattern, ast.MatchStar):
            names = [pattern.name]
            nested: list[ast.pattern] = []
        elif isinstance(pattern, ast.MatchClass):
            names = []
            nested = [*pattern.patterns, *pattern.kwd_patterns]
        else:
            return
        for name in names:
            if name is not None:
                self.aliases[name] = ""
        for item in nested:
            self._bind_pattern_captures(item, "", None)

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        previous_frozen, previous_deferred = self.frozen, self.deferred_frozen
        for item in node.items:
            # Context managers evaluate in entry order: a later manager runs
            # inside the travels entered before it, and the most recently
            # entered travel decides whether its expressions are pinned.
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.bind(item.optional_vars, item.context_expr)
            if self._is_travel(item.context_expr):
                if self._enters_travel(item.context_expr):
                    # A deterministic travel freezes every later expression in
                    # the entry list and the block itself.
                    self.frozen = True
                elif not self._keeps_inherited_freeze(item.context_expr):
                    # A ticking travel advances the clock from real elapsed
                    # time, so reads after it are not pinned to an instant.
                    self.frozen = False
        for statement in node.body:
            self.visit(statement)
        self.frozen, self.deferred_frozen = previous_frozen, previous_deferred

    visit_AsyncWith = visit_With

    def _visit_defaults(self, arguments: ast.arguments) -> dict[str, str]:
        """Evaluate the parameter defaults and return the bindings they capture.

        Defaults evaluate once, in order, at definition time, so a parameter
        that defaults to a clock callable holds it when the body starts, even
        if a later default rebinds the alias.
        """
        captured: dict[str, str] = {}
        positional = [*arguments.posonlyargs, *arguments.args]
        for parameter, default in zip(
            positional[len(positional) - len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        ):
            self.visit(default)
            captured[parameter.arg] = self.qualified(default)
        for parameter, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
            if default is not None:
                self.visit(default)
                captured[parameter.arg] = self.qualified(default)
        return captured

    def _visit_annotations(self, arguments: ast.arguments) -> None:
        if self.postponed:
            return
        if not self.probing:
            # The symbol table lists the keyword-only parameters' annotations
            # after the ``**kwargs`` annotation, while they evaluate before it.
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                arguments.vararg,
                arguments.kwarg,
                *arguments.kwonlyargs,
            ):
                if argument is not None and argument.annotation is not None:
                    self._preassign_expression_tables(argument.annotation)
        # Annotations evaluate in declaration order: positional parameters,
        # then the vararg, then keyword-only parameters and the kwarg.
        for argument in (*arguments.posonlyargs, *arguments.args):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        for argument in arguments.kwonlyargs:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)

    def _visit_arguments(self, arguments: ast.arguments) -> dict[str, str]:
        """Visit defaults always; annotations only when they execute."""
        captured = self._visit_defaults(arguments)
        self._visit_annotations(arguments)
        return captured

    def _enter_wrapper_scopes(self, name: str, lineno: int) -> list[symtable.SymbolTable]:
        """The wrapper tables a PEP 695 definition evaluates its contents in.

        A generic function evaluates its annotations inside its type-parameter
        scope, and a type alias nests its value inside the alias scope as
        well; the wrappers are returned outermost first. A plain definition
        has none.
        """
        entered: list[symtable.SymbolTable] = []
        table = self.table
        while True:
            children = self.children.setdefault(table, table.get_children())
            wrapper = next(
                (
                    child
                    for child in children
                    if child.get_name() == name
                    and child.get_lineno() == lineno
                    and child.get_type() not in {"function", "class"}
                ),
                None,
            )
            if wrapper is None:
                return entered
            entered.append(wrapper)
            table = wrapper

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self.probing:
            # The silent loop pass only collects bindings; the real pass
            # records the definitions.
            return
        # A mark or a fixture parameter only reaches what pytest manages: the
        # collected tests and the registered fixtures, not their helpers.
        # A function nested in another body is neither collected nor
        # registered.
        managed = self.collectable_scope and not self.in_function_body
        collected = node.name.startswith("test") and managed
        fixture = self._is_fixture(node.decorator_list) and managed
        own = (self._decorator_freezes(node.decorator_list) and collected and not fixture) or (
            (collected or fixture)
            and _takes_frozen_clock(node.args)
            and not self.inherited_parametrization
            and not self._parametrizes_directly(node.decorator_list)
        )
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.default_bindings[id(node)] = self._visit_defaults(node.args)
        entered = self._enter_wrapper_scopes(node.name, node.lineno)
        previous_table = self.table
        if entered:
            # Annotations of a generic function are evaluated inside its
            # type-parameter scope; its defaults stay in the enclosing one.
            self.table = entered[-1]
        self._visit_annotations(node.args)
        if node.returns is not None and not self.postponed:
            self.visit(node.returns)
        self.table = previous_table
        frozen = own or self.deferred_frozen
        self.functions.append((node, self.child_table(node, node.name), self.enclosing, frozen))
        self.aliases[node.name] = ""
        self.aliases[GENERATOR_KEY + node.name] = ""
        self.aliases[CALLABLE_KEY + node.name] = str(id(node))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if self.probing:
            return
        self.default_bindings[id(node)] = self._visit_arguments(node.args)
        # A lambda body runs at call time, which may be long after the
        # enclosing block exited, so only marker-derived deferred freeze
        # covers it — not the run-time ``frozen`` of the defining scope.
        frozen = self.deferred_frozen
        table = self.preassigned.pop(id(node), None)
        if table is None:
            table = self.child_table(node, "lambda")
        self.functions.append((node, table, self.enclosing, frozen))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.probing:
            return
        # A class mark reaches only the tests of a class pytest collects; a
        # helper class called from an unmarked test stays unfrozen.
        own = (
            node.name.startswith("Test")
            and self.collectable_scope
            and not self.in_function_body
            and (
                self._decorator_freezes(node.decorator_list)
                or self._scope_marker_freezes(node.body)
            )
        )
        for decorator in node.decorator_list:
            self.visit(decorator)
        previous_standing_table = self.table
        entered = self._enter_wrapper_scopes(node.name, node.lineno)
        if entered:
            # The bases and keywords of a generic class are evaluated inside
            # its type-parameter scope; its decorators run outside it.
            self.table = entered[-1]
        for expression in [*node.bases, *node.keywords]:
            self.visit(expression)
        if entered:
            self.table = previous_standing_table
        previous_table, previous_aliases = self.table, self.aliases
        previous_frozen, previous_deferred = self.frozen, self.deferred_frozen
        self.table = self.child_table(node, node.name)
        self.aliases = self.aliases.copy()
        # Class-body statements run at import; only the deferred methods run
        # under the class marker. A class statement executes wherever it
        # stands, so its body evaluates its own annotations again.
        previous_body_flag = self.deferred_body
        self.deferred_body = False
        self.deferred_frozen = self.deferred_frozen or own
        previous_collectable = self.collectable_scope
        self.collectable_scope = previous_collectable and node.name.startswith("Test")
        previous_parametrization = self.inherited_parametrization
        self.inherited_parametrization = (
            previous_parametrization
            or self._scope_parametrizes_directly(node.body, node.decorator_list)
        )
        for statement in node.body:
            self.visit(statement)
        self.inherited_parametrization = previous_parametrization
        self.collectable_scope = previous_collectable
        self.deferred_body = previous_body_flag
        self.table, self.aliases = previous_table, previous_aliases
        self.frozen, self.deferred_frozen = previous_frozen, previous_deferred
        self.aliases[node.name] = ""

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        entered = self._enter_wrapper_scopes(node.name.id, node.lineno)
        if not entered:
            self.visit(node.value)
            return
        # The alias value is evaluated inside the type-parameter scope and
        # the alias scope nested in it; enter both before visiting it.
        previous_table = self.table
        self.table = entered[-1]
        self.visit(node.value)
        self.table = previous_table

    def _preassign_comprehension_tables(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Reserve a comprehension's own tables in symbol-table order.

        The symbol table lists each generator level, then a dictionary's value
        scope and key scope or the element scope, while the scan evaluates the
        entries in source order. The first iterable is already evaluated.
        """
        self._preassign_generator_levels(node, first_iterable=False)
        if isinstance(node, ast.DictComp):
            self._preassign_expression_tables(node.value)
            self._preassign_expression_tables(node.key)
        else:
            self._preassign_expression_tables(node.elt)

    def _bind_targets_silently(self, targets: list[tuple[ast.expr, dict[str, str]]]) -> None:
        """Bind loop or comprehension targets without consuming target scopes.

        The scan visits each target again, and a scope in a target
        expression is consumed by that visit alone.
        """
        outer_probing, self.probing = self.probing, True
        try:
            for target, value in targets:
                self._bind_loop_target(target, value)
        finally:
            self.probing = outer_probing

    def _settle_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        """Scan a comprehension's iterations until the bindings stabilize."""
        # Each iteration rebinds the targets and re-evaluates the filters
        # under the previous iteration's bindings, so the scans repeat until
        # the bindings stabilize. Every pass's reads stay recorded, because
        # each iteration may see a different state; a pass whose entry state
        # equals the previous pass's entry state only repeats the tail that
        # the previous pass already recorded.
        previous_entry: dict[str, str] | None = None
        scan_start = len(self.reads)
        for _ in range(_PROBE_LIMIT):
            entry_state = dict(self.aliases)
            outer_probing = self.probing
            # Later passes re-visit definitions whose tables are already
            # consumed, so they scan silently; an inherited silent pass
            # stays silent throughout.
            self.probing = outer_probing or previous_entry is not None
            try:
                rejected = self._scan_comprehension_pass(node, 0)
            finally:
                self.probing = outer_probing
            if rejected:
                # Assignments in a skipped element cannot overwrite the
                # filter-exit states, and the successful element's own
                # assignments stay reachable too.
                self._merge_states([dict(self.aliases), *rejected])
            if entry_state == previous_entry:
                del self.reads[scan_start:]
                break
            previous_entry = entry_state
            scan_start = len(self.reads)

    def visit_comprehension_scope(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self._visit_iterable(node.generators[0].iter)
        if isinstance(node, ast.GeneratorExp) and not self.probing:
            first = node.generators[0]
            self.first_iterable_values[id(node)] = _loop_bindings(first.target, first.iter, self)
        previous_table, previous_aliases, previous_enclosing = (
            self.table,
            self.aliases,
            self.enclosing,
        )
        if isinstance(node, ast.GeneratorExp) and not self.probing:
            # The silent loop pass leaves the table for the real pass.
            preassigned = self.preassigned.pop(id(node), None)
            self.table = (
                preassigned if preassigned is not None else self.child_table(node, "genexpr")
            )
            # The body runs at consumption time, which may be long after the
            # enclosing block exited, so only the marker-derived freeze
            # covers it and its element scans with the deferred functions.
            self.functions.append((node, self.table, previous_enclosing, self.deferred_frozen))
            self.generator_scopes[id(node)] = dict(previous_aliases)
            if id(node) != self.assigned_generator:
                # Consumed in place, under the freeze active here.
                self.generator_frozen[id(node)] = self.frozen
        self.aliases = self.enclosing.copy()
        self.enclosing = self.aliases
        if not isinstance(node, ast.GeneratorExp) and not self.probing:
            self._preassign_comprehension_tables(node)
        assigned = self.walrus
        self.walrus = set()
        self._bind_targets_silently(
            [
                (generator.target, _loop_bindings(generator.target, generator.iter, self))
                for generator in node.generators
            ]
        )
        if isinstance(node, ast.GeneratorExp):
            # The filters and later iterables also run at consumption time,
            # so the deferred scan covers them with the element.
            self.walrus = assigned
            self.table, self.aliases, self.enclosing = (
                previous_table,
                previous_aliases,
                previous_enclosing,
            )
            # Consuming the generator may run its assignment expressions, so
            # the containing scope may hold their bindings from then on. A
            # generator bound to a name is consumed where the name is read;
            # any other generator is consumed in place.
            targets: list[tuple[ast.expr, dict[str, str] | ast.expr]] = [
                (
                    generator.target,
                    self.first_iterable_values.get(id(node), {}) if index == 0 else generator.iter,
                )
                for index, generator in enumerate(node.generators)
            ]
            bindings = [
                (named.target.id, named.value, targets)
                for named in _generator_named_expressions(node)
            ]
            if id(node) == self.assigned_generator:
                self.generator_bindings = bindings
            else:
                self._apply_generator_bindings(bindings)
            return
        self._settle_comprehension(node)
        leaked, self.walrus = self.walrus, assigned
        if not isinstance(node, ast.GeneratorExp):
            # A nested eager comprehension's walrus names must reach the
            # containing scope through the enclosing comprehension's own
            # handback.
            self.walrus.update(leaked)
        # An assignment expression binds in the containing scope, so the
        # comprehension's copy hands its walrus bindings back before the
        # temporary map is discarded.
        temporary = self.aliases
        self.table, self.aliases, self.enclosing = (
            previous_table,
            previous_aliases,
            previous_enclosing,
        )
        for name in leaked:
            if isinstance(node, ast.GeneratorExp):
                # An unconsumed generator never runs its body, so its walrus
                # bindings do not reach the containing scope yet.
                break
            if name in temporary:
                # The comprehension may run zero iterations or skip them all,
                # so its walrus bindings merge with the incoming ones instead
                # of replacing them.
                previous_aliases[name] = _merge_values(
                    [previous_aliases.get(name, ""), temporary[name]]
                )

    visit_ListComp = visit_comprehension_scope
    visit_SetComp = visit_comprehension_scope
    visit_DictComp = visit_comprehension_scope
    visit_GeneratorExp = visit_comprehension_scope

    def _parametrizes_directly(self, decorators: list[ast.expr]) -> bool:
        """Whether a parametrize mark supplies ``frozen_clock`` as a plain value.

        A directly parametrized argument replaces the fixture; an indirect
        one still requests it.
        """
        for decorator in decorators:
            if not (
                isinstance(decorator, ast.Call) and self.qualified(decorator.func) == PARAMETRIZE
            ):
                continue
            names = _parametrized_names(decorator)
            indirect = next(
                (keyword.value for keyword in decorator.keywords if keyword.arg == "indirect"), None
            )
            if FROZEN_CLOCK in names and not _indirect_covers(indirect):
                return True
        return False

    def _is_fixture(self, decorators: list[ast.expr]) -> bool:
        """Whether a decorator registers the function as a pytest fixture.

        Marks on a fixture function never reach the tests that request it,
        so a freeze marker there does not freeze its body.
        """
        return any(
            self.qualified(decorator.func if isinstance(decorator, ast.Call) else decorator)
            in FIXTURE_DECORATORS
            for decorator in decorators
        )

    def _decorator_freezes(self, decorators: list[ast.expr]) -> bool:
        return any(
            isinstance(decorator, ast.Call) and self.qualified(decorator.func) == FREEZE_MARKER
            for decorator in decorators
        )

    def _effective_pytestmark(self, statements: list[ast.stmt]) -> ast.expr | None:
        """The value of the scope's last ``pytestmark`` assignment.

        Markers govern test execution regardless of where they appear, so a
        scope's ``pytestmark`` is resolved before its functions are recorded;
        the last assignment is the one pytest collects.
        """
        effective: ast.expr | None = None
        for statement in statements:
            if isinstance(statement, ast.Assign):
                targets, value = statement.targets, statement.value
            elif isinstance(statement, ast.AnnAssign):
                targets, value = [statement.target], statement.value
            else:
                continue
            if value is not None and any(
                isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
            ):
                effective = value
        return effective

    def _scope_marker_freezes(self, statements: list[ast.stmt]) -> bool:
        """Whether the scope's effective ``pytestmark`` carries a freeze marker."""
        effective = self._effective_pytestmark(statements)
        return effective is not None and self._carries_freeze_marker(effective)

    def _scope_parametrizes_directly(
        self, statements: list[ast.stmt], decorators: list[ast.expr]
    ) -> bool:
        """Whether a scope's marks parametrize ``frozen_clock`` for the tests inside."""
        effective = self._effective_pytestmark(statements)
        marks = (
            list(effective.elts)
            if isinstance(effective, (ast.List, ast.Tuple, ast.Set))
            else [effective]
            if effective is not None
            else []
        )
        return self._parametrizes_directly([*decorators, *marks])

    def _carries_freeze_marker(self, value: ast.expr) -> bool:
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return any(
                isinstance(element, ast.Call) and self.qualified(element.func) == FREEZE_MARKER
                for element in value.elts
            )
        return isinstance(value, ast.Call) and self.qualified(value.func) == FREEZE_MARKER

    def _is_travel(self, context_expr: ast.expr) -> bool:
        return isinstance(context_expr, ast.Call) and self.qualified(context_expr.func) == TRAVEL

    def _keeps_inherited_freeze(self, context_expr: ast.expr) -> bool:
        """Whether ``travel(None, tick=False)`` keeps an inherited freeze.

        Inside a frozen travel the host-clock read on entry already sees the
        mocked clock, so the nested block stays deterministic; at the top level
        ``frozen`` is False anyway and the block stays reported.
        """
        if not (isinstance(context_expr, ast.Call) and self.qualified(context_expr.func) == TRAVEL):
            return False
        destination = (
            context_expr.args[0]
            if context_expr.args
            else next(
                (
                    keyword.value
                    for keyword in context_expr.keywords
                    if keyword.arg == "destination"
                ),
                None,
            )
        )
        tick = next(
            (keyword.value for keyword in context_expr.keywords if keyword.arg == "tick"), None
        )
        destination_is_none = isinstance(destination, ast.Constant) and destination.value is None
        ticks_false = isinstance(tick, ast.Constant) and tick.value is False
        return destination_is_none and ticks_false

    def _enters_travel(self, context_expr: ast.expr) -> bool:
        """Only a fixed, non-ticking travel is a deterministic instant.

        ``travel(...)`` defaults to ``tick=True`` (the clock advances from the
        destination) and ``travel(None)`` freezes at the host time read on
        entry — neither pins a repeatable instant, so neither suppresses.
        """
        if not (isinstance(context_expr, ast.Call) and self.qualified(context_expr.func) == TRAVEL):
            return False
        destination = (
            context_expr.args[0]
            if context_expr.args
            else next(
                (
                    keyword.value
                    for keyword in context_expr.keywords
                    if keyword.arg == "destination"
                ),
                None,
            )
        )
        tick = next(
            (keyword.value for keyword in context_expr.keywords if keyword.arg == "tick"), None
        )
        non_none_destination = not (
            isinstance(destination, ast.Constant) and destination.value is None
        )
        ticks_false = isinstance(tick, ast.Constant) and tick.value is False
        return non_none_destination and ticks_false

    def scan(self, tree: ast.Module) -> list[tuple[str, int]]:
        # Resolve module markers before the main pass so a ``pytestmark``
        # declared after the test functions still covers them; imports come
        # first so the marker expressions resolve through the alias map.
        for statement in tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                self.visit(statement)
                if (
                    isinstance(statement, ast.ImportFrom)
                    and statement.module == "__future__"
                    and any(alias.name == "annotations" for alias in statement.names)
                ):
                    # Postponed annotations never execute, so their scopes do
                    # not exist in the symbol table; visiting their expressions
                    # would look up symtable children that are not there.
                    self.postponed = True
        if self.module_marker_applies and self._scope_marker_freezes(tree.body):
            self.deferred_frozen = True
        self.inherited_parametrization = self._scope_parametrizes_directly(tree.body, [])
        self.visit(tree)
        self._scan_deferred_functions()
        # Repeated passes and nested levels can record the same read twice;
        # the findings count one read per line and name.
        return _unique_reads(self.reads)

    def _scan_deferred_functions(self) -> None:
        """Scan every recorded function, lambda and generator body."""
        for node, table, enclosing, recorded_frozen in self.functions:
            self.table = table
            scopes = (
                self.generator_scopes
                if isinstance(node, ast.GeneratorExp)
                else self.callable_scopes
            )
            captured = id(node) in scopes
            scope = scopes[id(node)] if captured else enclosing
            if captured and isinstance(node, ast.AsyncFunctionDef):
                # Referencing a coroutine function only creates the coroutine;
                # its body runs at a later await, so the enclosing scope's
                # final bindings stay reachable.
                scope = self._merged([scope, enclosing])
            self.aliases = scope.copy()
            frozen = (
                self.generator_frozen.get(id(node), recorded_frozen)
                if captured
                else recorded_frozen
            )
            for symbol in table.get_symbols():
                name = symbol.get_name()
                if symbol.is_local():
                    self.aliases[name] = ""
                    self.aliases[GENERATOR_KEY + name] = ""
                    self.aliases[CALLABLE_KEY + name] = ""
                elif symbol.is_global() and not (captured and name in self.aliases):
                    # A captured generator scope already holds the module
                    # bindings at consumption.
                    self.aliases[name] = self.module_aliases.get(name, "")
            self.aliases.update(self.default_bindings.get(id(node), {}))
            self.enclosing = self.aliases
            self.return_states = []
            self.frozen = frozen
            self.deferred_frozen = frozen
            previous_body_flag = self.deferred_body
            self.deferred_body = True
            self.in_function_body = True
            if isinstance(node, ast.Lambda):
                self.visit(node.body)
            elif isinstance(node, ast.GeneratorExp):
                # The body runs per consumption, so its levels settle the
                # same way an eager comprehension's do.
                self._preassign_comprehension_tables(node)
                self._settle_comprehension(node)
            else:
                for statement in node.body:
                    self.visit(statement)
            self.deferred_body = previous_body_flag
            self.in_function_body = False


def _named_expressions(node: ast.AST) -> Iterator[ast.NamedExpr]:
    """The assignment expressions that bind in the scope containing ``node``,
    in evaluation order.

    A lambda keeps its own assignment expressions to itself.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Lambda):
            continue
        yield from _named_expressions(child)
        if isinstance(child, ast.NamedExpr):
            yield child


def _generator_named_expressions(node: ast.GeneratorExp) -> Iterator[ast.NamedExpr]:
    """The assignment expressions consuming a generator runs, in order.

    The first iterable evaluates at creation; each iteration then runs a
    level's filters, the next level's iterable, and finally the element.
    """
    for index, generator in enumerate(node.generators):
        for expression in (*([generator.iter] if index else []), *generator.ifs):
            if isinstance(expression, ast.NamedExpr):
                yield from _named_expressions(expression)
                yield expression
            else:
                yield from _named_expressions(expression)
    yield from _named_expressions(node.elt)
    if isinstance(node.elt, ast.NamedExpr):
        yield node.elt


def _catches_everything(node: ast.Try | ast.TryStar) -> bool:
    """Whether a handler stops every exception the try body raises."""
    return any(
        handler.type is None
        or (isinstance(handler.type, ast.Name) and handler.type.id == "BaseException")
        for handler in node.handlers
    )


def _indirect_covers(indirect: ast.expr | None) -> bool:
    """Whether a parametrize ``indirect`` argument routes ``frozen_clock`` to the fixture."""
    if indirect is None:
        return False
    if isinstance(indirect, ast.Constant):
        return bool(indirect.value)
    if isinstance(indirect, (ast.List, ast.Tuple)):
        return any(
            isinstance(element, ast.Constant) and element.value == FROZEN_CLOCK
            for element in indirect.elts
        )
    # An argument that cannot be read statically keeps the exemption.
    return True


def _parametrized_names(decorator: ast.Call) -> list[str]:
    """The argument names a ``parametrize`` mark supplies."""
    names = decorator.args[0] if decorator.args else None
    if names is None:
        names = next(
            (keyword.value for keyword in decorator.keywords if keyword.arg == "argnames"), None
        )
    if isinstance(names, ast.Constant) and isinstance(names.value, str):
        return [name.strip() for name in names.value.split(",")]
    if isinstance(names, (ast.List, ast.Tuple)):
        return [
            element.value
            for element in names.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    return []


def _takes_frozen_clock(args: ast.arguments) -> bool:
    """Whether pytest injects the ``frozen_clock`` fixture into the function.

    Pytest requests a fixture for each positional-or-keyword and keyword-only
    parameter that has no default.
    """
    positional = [*args.posonlyargs, *args.args]
    undefaulted = [
        argument
        for argument in positional[: len(positional) - len(args.defaults)]
        if argument in args.args
    ]
    required_keyword_only = [
        argument
        for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is None
    ]
    return FROZEN_CLOCK in [argument.arg for argument in (*undefaulted, *required_keyword_only)]


@dataclasses.dataclass(frozen=True)
class ClockFindings:
    """Clock reads with their line numbers, plus the AQPY000 suppression tally."""

    reads: list[tuple[str, int]]
    suppressed_read_lines: frozenset[int]
    unreasoned_suppressions: int


def clock_findings(source: str, *, module_marker_applies: bool = True) -> ClockFindings:
    """Resolve clock-read bindings within each lexical scope in statement order.

    A read whose line carries a valid ``# noqa: AQPY001 - <reason> (#<issue>)``
    is reported in ``suppressed_read_lines`` and excluded from the count;
    AQPY000 validation stays separate.
    """
    visitor = ClockVisitor(source, module_marker_applies=module_marker_applies)
    reads = visitor.scan(ast.parse(source))
    unreasoned = 0
    suppressed: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        codes = NOQA_CODES.search(token.string)
        if codes is None:
            continue
        named = {*codes.group("codes").replace(" ", "").split(",")}
        if not named & GUARDED_CODES:
            continue
        if SUPERSEDED.search(token.string) is None:
            unreasoned += 1
        elif AQPY001 in named:
            suppressed.add(token.start[0])
    return ClockFindings(
        reads=reads,
        suppressed_read_lines=frozenset(suppressed),
        unreasoned_suppressions=unreasoned,
    )


def inventory(root: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in sorted((root / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        report = clock_findings(source, module_marker_applies=path.name != "conftest.py")
        unfrozen = [name for name, line in report.reads if line not in report.suppressed_read_lines]
        found = {AQPY001: len(unfrozen), AQPY000: report.unreasoned_suppressions}
        found = {rule: count for rule, count in found.items() if count}
        if found:
            counts[path.relative_to(root).as_posix()] = found
    return counts


class GuardArguments(argparse.Namespace):
    root: Path = BACKEND
    allowlist: Path = ALLOWLIST
    strict: bool = False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--root", type=Path, default=BACKEND)
    _ = parser.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    _ = parser.add_argument("--strict", action="store_true", help="Reject even allowlisted reads")
    args = parser.parse_args(argv, namespace=GuardArguments())
    root, allowlist, strict = args.root, args.allowlist, args.strict
    actual = inventory(root)
    expected = (
        {}
        if strict
        else TypeAdapter(dict[str, dict[str, int]]).validate_json(allowlist.read_text())
    )
    if actual != expected:
        for path in sorted(actual.keys() | expected.keys()):
            if actual.get(path) != expected.get(path):
                print(
                    f"{path}: unfrozen clock reads {actual.get(path, {})}; "
                    "allowlisted "
                    f"{expected.get(path, {})}. Freeze the clock "
                    "(@pytest.mark.freeze_time / frozen_clock / time_machine.travel) "
                    "or suppress with '# noqa: AQPY001 - <reason> (#<issue>)'."
                )
        return 1
    total = sum(sum(counts.values()) for counts in actual.values())
    if total:
        print(
            f"WARNING: {total} unfrozen clock reads in {len(actual)} test files remain "
            "allowlisted; freeze the clock or shrink the allowlist."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
