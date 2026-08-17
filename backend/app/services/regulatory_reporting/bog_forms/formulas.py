"""A small, safe evaluator for the official templates' formula vocabulary.

Discovery over every BoG template (24 workbooks, 5,903 formula cells) found
exactly: ``SUM``, ``IF``, and ``+ - * /`` with cell refs, ranges and cross-sheet
references. Evaluating the templates' OWN formulas over the mapped input cells
means every roll-up on an exported return is BoG's, never a re-implementation —
so AequorOS cannot "invent" or "simplify" a line.

The evaluator is deliberately not Excel: no other functions, no volatile state,
no external links. An unsupported construct raises ``UnsupportedFormulaError``
so the layout test catches new template revisions instead of silently mis-
computing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    get_column_letter,
)

Number = float


class UnsupportedFormulaError(ValueError):
    """The formula uses something outside the templates' vocabulary."""


@dataclass(frozen=True)
class CellRef:
    sheet: str | None
    ref: str
    #: External workbook index (``[1]Sheet!A1``) — a cross-FORM link; None for
    #: in-workbook references.
    external: int | None = None


#: External-workbook link prefix as LibreOffice/Excel serialise it: ``[1]BSD2!D38``
#: (BSD8 pulls loan totals straight from the BSD2 workbook — the Guide's "FROM
#: BSD2"). The evaluator exposes the workbook index on the CellRef so the engine
#: can resolve it against the dependent form's computed values.
_EXTERNAL = re.compile(r"^\[(\d+)\]")

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<pct>%)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>"[^"]*")
  | (?P<range>(?:\[\d+\])?(?:'[^']+'|[A-Za-z0-9_\-\. ]+?)?!?\$?[A-Z]{1,3}\$?\d+:\$?[A-Z]{1,3}\$?\d+)
  | (?P<ref>(?:\[\d+\])?(?:'[^']+'|[A-Za-z0-9_\-\.]+)!\$?[A-Z]{1,3}\$?\d+|\$?[A-Z]{1,3}\$?\d+)
  | (?P<func>[A-Z][A-Z0-9\.]*)(?=\()
  | (?P<op>[\+\-\*/\^&<>=]{1,2}|[(),])
    """,
    re.VERBOSE,
)


def _split_sheet(token: str) -> tuple[str | None, str, int | None]:
    """Split ``[1]'Sheet Name'!$A$1`` → (sheet, ref, external_index)."""
    external: int | None = None
    m = _EXTERNAL.match(token)
    if m:
        external = int(m.group(1))
        token = token[m.end() :]
    if "!" in token:
        sheet, ref = token.rsplit("!", 1)
        sheet = sheet.strip()
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1]
        return sheet, ref.replace("$", ""), external
    return None, token.replace("$", ""), external


def expand_range(a: str, b: str) -> list[str]:
    ca, ra = coordinate_from_string(a)
    cb, rb = coordinate_from_string(b)
    c0, c1 = sorted((column_index_from_string(ca), column_index_from_string(cb)))
    r0, r1 = sorted((ra, rb))
    return [f"{get_column_letter(c)}{r}" for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


class _Parser:
    """Recursive-descent parser → evaluates directly (the grammar is tiny)."""

    def __init__(self, text: str, resolve: Callable[[CellRef], Number | str | None]) -> None:
        self.tokens = self._tokenize(text)
        self.i = 0
        self.resolve = resolve

    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m:
                msg = f"unsupported formula syntax at {text[pos : pos + 12]!r} in {text!r}"
                raise UnsupportedFormulaError(msg)
            pos = m.end()
            kind = m.lastgroup
            if kind == "ws":
                continue
            out.append((kind or "", m.group(0)))
        return out

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise UnsupportedFormulaError("unexpected end of formula")
        if expected is not None and tok[1] != expected:
            msg = f"expected {expected!r} got {tok[1]!r}"
            raise UnsupportedFormulaError(msg)
        self.i += 1
        return tok

    # --- grammar -----------------------------------------------------------
    def expr(self) -> Number | str | bool:
        left = self.arith()
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("<", ">", "=", "<=", ">=", "<>"):
            self.take()
            right = self.arith()
            ln, rn = _num(left), _num(right)
            return {
                "<": ln < rn,
                ">": ln > rn,
                "=": ln == rn,
                "<=": ln <= rn,
                ">=": ln >= rn,
                "<>": ln != rn,
            }[tok[1]]
        return left

    def arith(self) -> Number | str:
        value = self.term()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("+", "-", "&"):
                self.take()
                rhs = self.term()
                if tok[1] == "&":
                    value = f"{value}{rhs}"
                elif tok[1] == "+":
                    value = _num(value) + _num(rhs)
                else:
                    value = _num(value) - _num(rhs)
            else:
                return value

    def term(self) -> Number | str:
        value = self.unary()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("*", "/"):
                self.take()
                rhs = self.unary()
                if tok[1] == "*":
                    value = _num(value) * _num(rhs)
                else:
                    denominator = _num(rhs)
                    value = 0.0 if denominator == 0 else _num(value) / denominator
            else:
                return value

    def unary(self) -> Number | str:
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("+", "-"):
            self.take()
            v = _num(self.unary())
            return -v if tok[1] == "-" else v
        return self.postfix()

    def postfix(self) -> Number | str:
        """Excel's ``%`` postfix operator (``6%`` → 0.06, ``D73%`` → D73/100)."""
        value = self.atom()
        while True:
            tok = self.peek()
            if tok and tok[0] == "pct":
                self.take()
                value = _num(value) / 100.0
            else:
                return value

    def atom(self) -> Number | str | bool:  # noqa: PLR0911
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1]
        if kind == "op" and text == "(":
            value = self.expr()
            self.take(")")
            return value
        if kind == "func":
            return self.call(text)
        if kind == "range":
            msg = f"bare range {text!r} outside SUM is not supported"
            raise UnsupportedFormulaError(msg)
        if kind == "ref":
            sheet, ref, external = _split_sheet(text)
            return _num_or_str(self.resolve(CellRef(sheet, ref, external)))
        msg = f"unexpected token {text!r}"
        raise UnsupportedFormulaError(msg)

    def call(self, name: str) -> Number | str | bool:  # noqa: PLR0912
        self.take("(")
        if name == "SUM":
            total = 0.0
            while True:
                tok = self.peek()
                if tok and tok[0] == "range":
                    self.take()
                    sheet, rng, external = _split_sheet(tok[1])
                    a, b = rng.split(":")
                    for ref in expand_range(a, b):
                        total += _num(self.resolve(CellRef(sheet, ref, external)))
                else:
                    total += _num(self.expr())
                nxt = self.take()
                if nxt[1] == ")":
                    return total
                if nxt[1] != ",":
                    raise UnsupportedFormulaError(f"bad SUM argument list near {nxt[1]!r}")
        if name == "IF":
            cond = self.expr()
            self.take(",")
            when_true = self.expr()
            when_false: Number | str | bool = 0.0
            nxt = self.take()
            if nxt[1] == ",":
                when_false = self.expr()
                self.take(")")
            elif nxt[1] != ")":
                raise UnsupportedFormulaError("bad IF argument list")
            return when_true if bool(cond) else when_false
        if name in ("ROUND", "ABS", "MAX", "MIN"):
            args: list[Number] = [_num(self.expr())]
            while self.take()[1] == ",":
                args.append(_num(self.expr()))
            if name == "ROUND":
                return round(args[0], int(args[1]) if len(args) > 1 else 0)
            if name == "ABS":
                return abs(args[0])
            return max(args) if name == "MAX" else min(args)
        msg = f"function {name} is outside the BoG template vocabulary"
        raise UnsupportedFormulaError(msg)


def _num(value: Number | str | bool | None) -> Number:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return float(value)


def _num_or_str(value: Number | str | bool | None) -> Number | str:
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return _num(value)


def evaluate(
    formula: str, resolve: Callable[[CellRef], Number | str | None]
) -> Number | str | bool | None:
    """Evaluate ``formula`` (with or without a leading ``=``).

    A bare ``=`` (an empty formula cell — BSD7A carries one) evaluates to None,
    i.e. a blank cell, exactly as Excel shows it.
    """
    text = formula[1:] if formula.startswith("=") else formula
    if not text.strip():
        return None
    parser = _Parser(text, resolve)
    value = parser.expr()
    if parser.peek() is not None:
        raise UnsupportedFormulaError(f"trailing tokens in {formula!r}")
    return value


#: Resolver for cross-FORM links: ``(external_index, sheet, ref) -> value``.
ExternalResolver = Callable[[int, str, str], Number | str | None]


class WorkbookEvaluator:
    """Evaluate every formula cell of a workbook given its input-cell values.

    ``inputs`` maps ``(sheet, ref) -> value``; formulas are resolved lazily and
    memoised, and cross-sheet references walk the same graph. Cycles raise.
    ``external`` resolves ``[n]Sheet!Ref`` links to OTHER forms' computed values
    (BSD8 → BSD2); when absent such a link evaluates as 0 and the engine flags
    the dependency as unresolved.
    """

    def __init__(
        self,
        formulas: Mapping[tuple[str, str], str],
        inputs: Mapping[tuple[str, str], Number | str | None],
        external: ExternalResolver | None = None,
    ) -> None:
        self.formulas = dict(formulas)
        self.inputs = dict(inputs)
        self.external = external
        self.unresolved_external: list[tuple[str, str, str]] = []
        #: in-workbook precedents of each formula cell, recorded while evaluating
        #: (lets the engine propagate per-cell unit flags: a formula over only
        #: unscaled inputs is itself unscaled)
        self.precedents: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self._cache: dict[tuple[str, str], Number | str | bool | None] = {}
        self._visiting: set[tuple[str, str]] = set()
        self._stack: list[tuple[str, str]] = []

    def _resolve(self, current_sheet: str, cr: CellRef) -> Number | str | None:
        if cr.external is None and self._stack:
            self.precedents.setdefault(self._stack[-1], set()).add(
                (cr.sheet or current_sheet, cr.ref)
            )
        if cr.external is not None:
            sheet = cr.sheet or current_sheet
            if self.external is None:
                self.unresolved_external.append((current_sheet, sheet, cr.ref))
                return 0.0
            return self.external(cr.external, sheet, cr.ref)
        return self.value(cr.sheet or current_sheet, cr.ref)

    def value(self, sheet: str, ref: str) -> Number | str | bool | None:
        key = (sheet, ref)
        if key in self._cache:
            return self._cache[key]
        if key in self.formulas:
            if key in self._visiting:
                msg = f"circular reference at {sheet}!{ref}"
                raise UnsupportedFormulaError(msg)
            self._visiting.add(key)
            self._stack.append(key)
            try:
                result = evaluate(self.formulas[key], lambda cr, _s=sheet: self._resolve(_s, cr))
            finally:
                self._stack.pop()
                self._visiting.discard(key)
            self._cache[key] = result
            return result
        return self.inputs.get(key)

    def evaluate_all(self) -> dict[tuple[str, str], Number | str | bool | None]:
        return {key: self.value(*key) for key in self.formulas}


# ---------------------------------------------------------------------------
# Unit algebra: is a formula cell MONEY (scale by the sheet unit on export) or
# UNITLESS (a count/percent/ratio — never scale)? Walks the same grammar with
# unit tags instead of numbers. Rules: money ± money → money; money × unitless →
# money; unitless × unitless → unitless; money ÷ money → UNITLESS (a ratio, e.g.
# CAR% = capital / RWA × 100, "% of total"); money ÷ unitless → money; SUM →
# money if any operand is money; IF → money if either branch is money;
# comparisons and string literals → unitless; a bare number → unitless.
# ---------------------------------------------------------------------------

MONEY = "money"
UNITLESS = "unitless"


class _UnitParser(_Parser):
    """Same grammar as :class:`_Parser`; ``resolve`` returns a unit tag."""

    def expr(self) -> str:  # type: ignore[override]
        left = self.arith()
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("<", ">", "=", "<=", ">=", "<>"):
            self.take()
            self.arith()
            return UNITLESS
        return left

    def arith(self) -> str:  # type: ignore[override]
        unit = self.term()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("+", "-", "&"):
                self.take()
                rhs = self.term()
                unit = MONEY if MONEY in (unit, rhs) else UNITLESS
            else:
                return unit

    def term(self) -> str:  # type: ignore[override]
        unit = self.unary()
        while True:
            tok = self.peek()
            if tok and tok[0] == "op" and tok[1] in ("*", "/"):
                self.take()
                rhs = self.unary()
                if tok[1] == "*":
                    unit = MONEY if MONEY in (unit, rhs) else UNITLESS
                elif unit == MONEY and rhs == MONEY:
                    unit = UNITLESS  # a ratio of two amounts
                elif unit == MONEY:
                    unit = MONEY
                else:
                    unit = UNITLESS
            else:
                return unit

    def unary(self) -> str:  # type: ignore[override]
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("+", "-"):
            self.take()
            return self.unary()
        return self.postfix()

    def postfix(self) -> str:  # type: ignore[override]
        unit = self.atom()
        while True:
            tok = self.peek()
            if tok and tok[0] == "pct":
                self.take()
            else:
                return unit

    def atom(self) -> str:  # type: ignore[override]
        kind, text = self.take()
        if kind in ("num", "str"):
            return UNITLESS
        if kind == "op" and text == "(":
            unit = self.expr()
            self.take(")")
            return unit
        if kind == "func":
            return self.call(text)
        if kind == "range":
            msg = f"bare range {text!r} outside SUM is not supported"
            raise UnsupportedFormulaError(msg)
        if kind == "ref":
            sheet, ref, external = _split_sheet(text)
            return str(self.resolve(CellRef(sheet, ref, external)))
        msg = f"unexpected token {text!r}"
        raise UnsupportedFormulaError(msg)

    def call(self, name: str) -> str:  # type: ignore[override]  # noqa: PLR0912
        self.take("(")
        if name == "SUM":
            unit = UNITLESS
            while True:
                tok = self.peek()
                if tok and tok[0] == "range":
                    self.take()
                    sheet, rng, external = _split_sheet(tok[1])
                    a, b = rng.split(":")
                    for ref in expand_range(a, b):
                        if str(self.resolve(CellRef(sheet, ref, external))) == MONEY:
                            unit = MONEY
                elif self.expr() == MONEY:
                    unit = MONEY
                nxt = self.take()
                if nxt[1] == ")":
                    return unit
                if nxt[1] != ",":
                    raise UnsupportedFormulaError(f"bad SUM argument list near {nxt[1]!r}")
        if name == "IF":
            self.expr()
            self.take(",")
            when_true = self.expr()
            when_false = UNITLESS
            nxt = self.take()
            if nxt[1] == ",":
                when_false = self.expr()
                self.take(")")
            elif nxt[1] != ")":
                raise UnsupportedFormulaError("bad IF argument list")
            return MONEY if MONEY in (when_true, when_false) else UNITLESS
        if name in ("ROUND", "ABS", "MAX", "MIN"):
            units = [self.expr()]
            while self.take()[1] == ",":
                units.append(self.expr())
            return MONEY if MONEY in units else UNITLESS
        msg = f"function {name} is outside the BoG template vocabulary"
        raise UnsupportedFormulaError(msg)


def formula_unit(formula: str, resolve: Callable[[CellRef], str]) -> str:
    """MONEY or UNITLESS for ``formula`` given each referenced cell's unit."""
    text = formula[1:] if formula.startswith("=") else formula
    if not text.strip():
        return UNITLESS
    parser = _UnitParser(text, resolve)  # type: ignore[arg-type]
    return parser.expr()


def workbook_units(
    formulas: Mapping[tuple[str, str], str],
    unitless_inputs: set[tuple[str, str]],
    depends_on_order: Sequence[str] = (),
) -> set[tuple[str, str]]:
    """The UNITLESS formula cells of a workbook (fixed point over the graph).

    Inputs are MONEY unless listed in ``unitless_inputs`` (counts, percents,
    foreign-currency units — the line map's ``unscaled`` cells). External links
    are treated as MONEY (they point at balance-sheet totals).
    """
    cache: dict[tuple[str, str], str] = {}
    visiting: set[tuple[str, str]] = set()

    def unit_of(sheet: str, ref: str) -> str:
        key = (sheet, ref)
        if key in cache:
            return cache[key]
        if key in formulas:
            if key in visiting:
                return MONEY
            visiting.add(key)
            try:
                result = formula_unit(
                    formulas[key],
                    lambda cr, _s=sheet: (
                        MONEY if cr.external is not None else unit_of(cr.sheet or _s, cr.ref)
                    ),
                )
            except UnsupportedFormulaError:
                result = MONEY
            finally:
                visiting.discard(key)
            cache[key] = result
            return result
        return UNITLESS if key in unitless_inputs else MONEY

    return {key for key in formulas if unit_of(*key) == UNITLESS}
