"""Absence is not acceptance (enterprise audit 2026-08-20, P0-11).

``ingestion._persist_canonical`` resolved each record's validation status with
``record_statuses.get(key, "accepted")``. A canonical record for which
validation produced NO status was therefore persisted as *accepted* and was
thereafter indistinguishable — in every engine and every filed return — from a
record that genuinely passed.

The default is now ``pending``: a status every calculation reader excludes (all
of them admit only ``("accepted", "warning")``), so an unvalidated row is
persisted, visible and traceable, but cannot reach a regulatory number.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as models_module
from app.api.deps import TenantContext
from app.domain.ingestion.constants import (
    INCLUDED_VALIDATION_STATUSES,
    VALIDATION_STATUSES,
)
from app.models import Bank, BankReportingPeriod, CanonicalPositionSnapshot
from app.services import ingestion, regulatory_reporting
from app.services.fact_derivation import _INCLUDED_VALIDATION_STATUSES
from app.services.regulatory_reporting.bog_forms.sources import ResolveContext, get_resolver
from tests.factories.canonical import seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)


def test_unvalidated_status_is_a_real_status_that_no_engine_reads() -> None:
    assert ingestion.UNVALIDATED_STATUS in VALIDATION_STATUSES
    assert ingestion.UNVALIDATED_STATUS not in INCLUDED_VALIDATION_STATUSES
    # The derivation's own constant must BE the shared scope, not a copy that
    # happens to agree with it today (re-audit D-4: the literal was duplicated
    # into eighteen module-private constants and the reporting layer simply
    # omitted it).
    assert _INCLUDED_VALIDATION_STATUSES == INCLUDED_VALIDATION_STATUSES


def test_absent_status_persists_as_pending_not_accepted() -> None:
    """The exact defect P0-11 names, at the line it names."""
    record_statuses = {("position", "KNOWN"): "warning"}

    def status_of(entity_type: str, source_reference: str) -> str:
        return record_statuses.get((entity_type, source_reference), ingestion.UNVALIDATED_STATUS)

    assert status_of("position", "KNOWN") == "warning"
    assert status_of("position", "NEVER_VALIDATED") == ingestion.UNVALIDATED_STATUS
    assert status_of("position", "NEVER_VALIDATED") != "accepted"


@dataclass(frozen=True)
class _Record:
    source_reference: str


@dataclass(frozen=True)
class _Records:
    gl_accounts: tuple[_Record, ...] = ()
    counterparties: tuple[_Record, ...] = ()
    products: tuple[_Record, ...] = ()
    positions: tuple[_Record, ...] = ()
    loan_events: tuple[_Record, ...] = ()


def test_unvalidated_records_are_counted_per_entity_type() -> None:
    records = _Records(
        positions=(_Record("P1"), _Record("P2"), _Record("P3")),
        products=(_Record("PR1"),),
    )
    statuses = {("position", "P1"): "accepted"}

    missing = ingestion._unvalidated_records(records, statuses)  # type: ignore[arg-type]

    assert missing == {"position": 2, "product": 1}


def test_a_fully_validated_batch_reports_no_unvalidated_records() -> None:
    """The healthy path: ``run_validation`` seeds a status for every record."""
    records = _Records(positions=(_Record("P1"),), gl_accounts=(_Record("G1"),))
    statuses = {("position", "P1"): "accepted", ("gl_account", "G1"): "accepted"}

    assert ingestion._unvalidated_records(records, statuses) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The filed-return half of the claim (forensic re-audit 2026-08-22, D-4)
# ---------------------------------------------------------------------------


def test_the_shared_bog_position_resolver_reads_the_same_scope_as_the_engines(
    db_session: Session,
) -> None:
    """``positions.sum`` is the resolver behind BSD2 and BSD5A.

    Until 2026-08-22 it filtered the current generation
    (``superseded_by``/``withdrawn_at``) but NOT ``validation_status``, so the
    Capital Adequacy Return read canonical rows the capital engine refuses —
    while the module docstring on ``ingestion.status_of`` asserted the exclusion
    held "in every engine and every filed return". Only 2 of the 14
    ``sources_ext`` modules carried the predicate.

    The fixture's ``LOAN/BAD`` snapshot is deliberately enormous
    (888,000,000) and carries ``validation_status="error"``, so a leak is
    unmistakable rather than a rounding argument.
    """
    materialize_canonical_test_book(db_session)
    db_session.flush()
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    period = db_session.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert period is not None
    # The resolver reads "latest snapshot on or before the period end", so the
    # canonical book is seeded AT the period end to be in scope at all.
    seed_canonical_fixture(
        db_session,
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        as_of=period.period_end,
    )
    db_session.flush()

    rejected = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.organization_id == DEMO_ORG_ID,
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
            CanonicalPositionSnapshot.validation_status == "error",
        )
    )
    assert rejected is not None, "the fixture must carry a rejected row for this to mean anything"

    rc = ResolveContext(
        db=db_session,
        ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
        bank=bank,
        period=period,
        column="total",
    )
    total = get_resolver("positions.sum")(rc, {"position_types": ["LOAN"]})

    assert total is not None
    assert Decimal(str(total)) > 0, "the resolver must still read the accepted book"
    assert Decimal(str(total)) < Decimal("888000000"), (
        "the rejected 888,000,000 snapshot reached a filed BoG return line"
    )


def _guarded_models() -> set[str]:
    """Canonical entities that HAVE a ``validation_status`` column, from the ORM.

    Derived, never listed: a canonical entity declared tomorrow is in scope the
    moment it inherits the column. ``CanonicalReferenceRow`` and
    ``CanonicalWithdrawal`` fall out because they genuinely do not carry it —
    confirmed against the primary, where ``canonical_reference_rows`` has twelve
    columns and none is ``validation_status`` — not because anyone exempted them.
    """
    guarded: set[str] = set()
    for name in dir(models_module):
        candidate = getattr(models_module, name)
        if not hasattr(candidate, "__tablename__"):
            continue
        try:
            columns = {column.key for column in sa_inspect(candidate).columns}
        except Exception:  # pragma: no cover - not a mapped class
            continue
        if "validation_status" in columns:
            guarded.add(name)
    return guarded


def _model_aliases(function: ast.AST, guarded: set[str]) -> set[str]:
    """Local rebindings such as ``snap = CanonicalPositionSnapshot``.

    BSD4 and BSD8 alias the snapshot class to keep their queries readable. A
    name-based scan that does not follow the alias silently stops checking those
    queries — which is how a regex-based architecture guard came to miss their
    missing ``withdrawn_at``. An unresolved alias must not read as compliance.
    """
    aliases: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in guarded:
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return aliases


def _exclusion_subquery_names(function: ast.AST) -> set[str]:
    """Names passed to ``not_in`` / ``notin_`` — i.e. built to be EXCLUDED.

    The admitted-scope predicate is fail-closed on an inclusion query: narrowing
    the population can only drop rows from a filed line. On a NOT-IN set the
    logic inverts — every predicate added SHRINKS the exclusion set and therefore
    ADMITS more rows to the return. ``positions.sum``'s ``counterparty_types_not``
    set is the only such query in the package: it names the counterparties whose
    positions the line must omit, so leaving it unfiltered excludes on any
    generation of the counterparty, which is the conservative direction. Adding
    the predicate there would let a position hang off a superseded row of an
    excluded type back into a filed BoG line. Exempt by REASON, not by file name.
    """
    names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"not_in", "notin_"}:
            continue
        names.update(a.id for a in node.args if isinstance(a, ast.Name))
    return names


def _query_units(function: ast.AST) -> list[tuple[int, ast.AST, str | None]]:
    """Every statement whose value builds a ``select()``, with its target name.

    Per STATEMENT, not per function: BSD11's bug was a ``latest`` subquery with
    no status predicate sitting in the same function as a fully-scoped outer
    query, so a function-level check reported it clean.
    """
    units: list[tuple[int, ast.AST, str | None]] = []
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return, ast.Expr)):
            continue
        value = node.value
        if value is None:
            continue
        if not any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "select"
            for c in ast.walk(value)
        ):
            continue
        target = None
        if isinstance(node, ast.Assign):
            target = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
        units.append((node.lineno, value, target))
    return units


def test_every_filed_return_query_carries_the_shared_accepted_scope() -> None:
    """The RULE behind the repair (forensic re-audit D-4; the lesson of D-9).

    D-4 was closed by adding the predicate to the resolver functions the audit
    named. That fixes the instances someone happened to enumerate and leaves
    nothing behind that can find the next one — the failure mode D-9 describes
    and D-14 repeated. A fifteenth ``sources_ext`` module, or one new ``select()``
    inside an existing one, would reintroduce D-4 in full with every suite green,
    because the negative control above pins exactly ONE resolver
    (``positions.sum``) and nothing looks at the other eighteen queries.

    It is not hypothetical. Walking the package per STATEMENT rather than per
    function found BSD11's ``latest`` subquery computing "the newest snapshot
    date" over rows the outer query then refuses — so a facility whose newest
    snapshot was unvalidated dropped out of the return altogether instead of
    falling back to its last accepted snapshot. Every sibling module already had
    it right, and no existing test could see it.

    Both inputs are derived: guarded models from the ORM mapper, modules from
    walking ``app/services/regulatory_reporting``. The
    ``INCLUDED_VALIDATION_STATUSES`` requirement is on the SHARED name — a
    module-private ``("accepted", "warning")`` that agrees today is how eighteen
    copies drifted apart and how the reporting layer came to omit the scope
    entirely while the engines kept it.
    """
    guarded = _guarded_models()
    assert {"CanonicalPositionSnapshot", "CanonicalGlAccount"} <= guarded
    assert "CanonicalReferenceRow" not in guarded, (
        "canonical_reference_rows grew a validation_status column; "
        "sources.reference_rows must now filter it"
    )

    package = Path(regulatory_reporting.__file__).parent
    required = ("validation_status", "superseded_by", "withdrawn_at")
    offenders: list[str] = []
    exempted: list[str] = []
    checked = 0
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            in_scope = guarded | _model_aliases(function, guarded)
            excluded_sets = _exclusion_subquery_names(function)
            for lineno, unit, target in _query_units(function):
                names = {n.id for n in ast.walk(unit) if isinstance(n, ast.Name)}
                if not names & in_scope:
                    continue
                where = f"{path.relative_to(package.parent)}:{lineno} {function.name}()"
                if target is not None and target in excluded_sets:
                    exempted.append(where)
                    continue
                checked += 1
                attributes = {n.attr for n in ast.walk(unit) if isinstance(n, ast.Attribute)}
                missing = [p for p in required if p not in attributes]
                if "INCLUDED_VALIDATION_STATUSES" not in names:
                    missing.append("the SHARED INCLUDED_VALIDATION_STATUSES, not a private copy")
                if missing:
                    offenders.append(
                        f"{where} queries {sorted(names & in_scope)} without {missing}"
                    )

    assert checked >= 20, (
        f"only {checked} filed-return queries were examined — the walker stopped "
        "seeing the BoG resolvers, so this gate has gone vacuous"
    )
    assert len(exempted) == 1, (
        "the NOT-IN exemption is meant to cover exactly the counterparty-type "
        f"exclusion set in positions.sum; it now covers {exempted}. A new "
        "exclusion subquery needs the reasoning in _exclusion_subquery_names "
        "re-checked, not the count bumped."
    )
    assert not offenders, (
        "a filed BoG return would read canonical rows the calculation engines "
        "refuse (forensic re-audit D-4): " + "; ".join(offenders)
    )


def test_every_module_that_reads_the_canonical_book_also_states_what_it_refused() -> None:
    """The other half of D-4, as a rule rather than as three call sites.

    Excluding the rows was the fix; excluding them SILENTLY is the same defect
    pointed the other way. A return compiled while rows sit in ``pending``,
    ``error`` or ``blocked`` reports a smaller figure, the template's own
    formulas subtotal the understated number faithfully, and nothing on the
    artifact tells the officer who signs it.

    Three families read the canonical book directly — ``bog_form``,
    ``large_exposures`` and ``lmt`` — and a fourth would be added by anyone
    writing one new resolver module. So the requirement is stated over the
    package, not over a list: a module that queries a guarded canonical entity
    must be covered by ``common.unvalidated_book_finding``, either in itself or
    in the ``generation.py`` of a package that encloses it (which is where the
    ``bog_form`` resolvers' disclosure is assembled, one level up from the
    ``sources_ext`` modules that do the querying).

    ``common.py`` satisfies this trivially and correctly: it holds the inverse
    query that MEASURES the exclusion, and it defines the finding.
    """
    package = Path(regulatory_reporting.__file__).parent
    guarded = _guarded_models()
    disclosure = "unvalidated_book_finding"
    offenders: list[str] = []
    reading_modules: list[str] = []

    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        reads_book = False
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            in_scope = guarded | _model_aliases(function, guarded)
            for _lineno, unit, _target in _query_units(function):
                names = {n.id for n in ast.walk(unit) if isinstance(n, ast.Name)}
                if names & in_scope:
                    reads_book = True
        if not reads_book:
            continue
        reading_modules.append(str(path.relative_to(package.parent)))

        covering = [path]
        parent = path.parent
        while parent != package.parent:
            covering.append(parent / "generation.py")
            parent = parent.parent
        if not any(p.exists() and disclosure in p.read_text() for p in covering):
            offenders.append(
                f"{path.relative_to(package.parent)} queries the canonical book but "
                f"neither it nor any enclosing generation.py calls {disclosure}()"
            )

    assert len(reading_modules) >= 9, (
        f"only {reading_modules} were seen to read the canonical book — the walker "
        "has stopped finding the resolvers, so this gate has gone vacuous"
    )
    assert not offenders, (
        "a filed return would exclude unvalidated canonical rows without saying so "
        "(forensic re-audit D-4, second half): " + "; ".join(offenders)
    )
