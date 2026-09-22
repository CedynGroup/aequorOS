"""The BI catalogue may only say what the platform already declares (spec §Phase 1).

Every ``certified_engine`` measure is a COPY of a registered figure: it must
resolve to exactly one primary authority for its ``(metric_id, regime)``, to a
live module the scoping authority (``module_scope``) knows how to gate, and it
carries that authority's designation verbatim — an advisory or supervisory
figure is never badged certified (D-022), and an unregistered one cannot be a
measure at all (H-009). Every member binds to a real mart column, every module
and sensitivity is a value the authorization enums accept, and no threshold is
a number. The domain package itself stays pure: no service, no model, no
session, no SQL.

The ``(table, column)`` check imports ``app.models.bi``. Until that module
lands (Agent C1, same wave) the test is RED, deliberately — a skipped guard
would let a mistyped column reach the compiler.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import fields
from pathlib import Path

import pytest

from app.core.authorization import Module, ModuleScope, Sensitivity
from app.db.base import Base
from app.domain.authority.registry import REGISTRY, Regime, UnknownMetricError
from app.domain.bi import extract
from app.domain.bi.authority import (
    AUTHORIZATION_MODULE,
    ENGINE_MODULES,
    ENTITLEMENT_SLUG,
    designation_of,
    engine_module_for,
)
from app.domain.bi.catalogue import Catalogue, MeasureDef, catalogue
from app.domain.bi.catalogue.members import SENSITIVITIES
from app.services import module_scope

BACKEND = Path(__file__).parents[2]
BI_DOMAIN = BACKEND / "app" / "domain" / "bi"

#: What the pure BI domain may never import: the service plane, the ORM, SQL.
FORBIDDEN_IMPORT_PREFIXES = ("app.services", "app.models", "app.api", "app.features", "sqlalchemy")


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


def _engine_measures(cat: Catalogue) -> list[MeasureDef]:
    measures = cat.engine_measures()
    assert measures, "the catalogue exposes no engine measures"
    return list(measures)


def _regime_of(measure: MeasureDef) -> Regime:
    return Regime(measure.id.split(".")[2])


# --- registry resolution ------------------------------------------------------------------


def test_every_engine_measure_resolves_to_exactly_one_primary_authority(cat: Catalogue) -> None:
    for measure in _engine_measures(cat):
        assert measure.engine_rule is not None, measure.id
        regime = _regime_of(measure)
        try:
            entry = REGISTRY.primary_for(measure.engine_rule.metric_id, regime=regime)
        except UnknownMetricError as exc:  # pragma: no cover - the failure message is the point
            raise AssertionError(f"{measure.id}: {exc}") from exc
        primaries = [
            e
            for e in REGISTRY.for_metric(measure.engine_rule.metric_id)
            if e.regime is regime and e.is_primary
        ]
        assert primaries == [entry], measure.id
        assert engine_module_for(entry) == measure.engine_rule.module, measure.id
        assert designation_of(entry) == measure.advisory_designation, measure.id


def test_every_engine_measure_resolves_to_a_module_scope_applicability(cat: Catalogue) -> None:
    """The live module is one ``module_scope.runs_module`` can decide for a tenant."""
    for module in ENGINE_MODULES:
        assert ENTITLEMENT_SLUG[module] == module_scope.MODULE_SCOPE_KEY[module], (
            f"the catalogue's entitlement slug for {module} drifted from module_scope"
        )
    for measure in _engine_measures(cat):
        assert measure.engine_rule is not None
        module = measure.engine_rule.module
        assert module in ENGINE_MODULES, measure.id
        assert module in module_scope.MODULE_SCOPE_KEY, measure.id
        assert measure.entitlement == module_scope.MODULE_SCOPE_KEY[module], measure.id
        assert measure.module == AUTHORIZATION_MODULE[module], measure.id


def test_no_advisory_supervisory_or_unregistered_measure_is_certified(cat: Catalogue) -> None:
    for measure in cat.measures():
        if measure.advisory_designation in (
            "advisory_only",
            "supervisory_monitoring",
            "unregistered",
        ):
            assert measure.certified is False, measure.id
        if measure.engine_rule is not None and measure.engine_rule.tier == "live":
            assert measure.certified is False, measure.id
    # An engine measure never carries the designation the registry cannot express.
    for measure in _engine_measures(cat):
        assert measure.advisory_designation in ("filed", "supervisory_monitoring", "advisory_only")
    assert any(m.certified for m in cat.measures()), "nothing is certifiable at all"


# --- vocabulary -----------------------------------------------------------------------------


def test_every_module_and_sensitivity_is_an_authorization_enum_value(cat: Catalogue) -> None:
    modules = {m.value for m in Module}
    scopes = {s.value for s in ModuleScope}
    sensitivities = {s.value for s in Sensitivity}
    assert set(SENSITIVITIES) == sensitivities
    for member in cat.members():
        assert member.module in modules, (member.id, member.module)
        assert member.module in scopes, (member.id, member.module)
        assert member.sensitivity in sensitivities, (member.id, member.sensitivity)
    assert Module("credit") is Module.CREDIT  # the CREDIT module the credit members rely on
    assert set(AUTHORIZATION_MODULE.values()) <= modules


def test_member_ids_are_unique(cat: Catalogue) -> None:
    ids = [member.id for member in cat.members()]
    assert len(ids) == len(set(ids))


def test_no_measure_declares_a_numeric_threshold(cat: Catalogue) -> None:
    for measure in cat.measures():
        source = measure.thresholds_source
        if source is not None:
            assert isinstance(source, str), measure.id
            assert re.fullmatch(r"[a-z][a-z0-9_]*", source), (measure.id, source)
            assert not re.fullmatch(r"[0-9.]+", source), (measure.id, source)
        for row_filter in measure.row_filters:
            for value in row_filter.values:
                assert isinstance(value, str) and not re.fullmatch(r"[0-9.]+", value), (
                    measure.id,
                    value,
                )


def test_catalogue_source_contains_no_currency_or_regulator_literal() -> None:
    """Jurisdiction neutrality: the reporting unit is the bank's, not a code word."""
    pattern = re.compile(r"\b(GHS|NGN|KES|ZAR|EUR|GBP|BoG|Bank of Ghana|cedi)\b")
    for path in sorted(BI_DOMAIN.rglob("*.py")):
        source = re.sub(r'"""(?:.|\n)*?"""', "", path.read_text(encoding="utf-8"))
        for number, raw in enumerate(source.splitlines(), 1):
            line = raw.strip()
            if line.startswith("#"):
                continue
            assert not pattern.search(line), f"{path.relative_to(BACKEND)}:{number}: {line}"


# --- mart binding -----------------------------------------------------------------------------


def test_every_member_binds_to_a_mapped_bi_column(cat: Catalogue) -> None:
    """RED until ``app.models.bi`` lands (Agent C1) — never skipped."""
    try:
        bi_models = importlib.import_module("app.models.bi")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "app.models.bi is not present yet; every catalogue (table, column) is unverified"
        ) from exc
    tables = Base.metadata.tables
    assert bi_models is not None
    for member in cat.members():
        assert member.table in tables, (member.id, member.table)
        assert member.column in tables[member.table].columns, (
            member.id,
            member.table,
            member.column,
        )


#: Columns the builder stamps at write time; the extractor never produces them.
BUILDER_STAMP = {"builder_version", "built_at"}


@pytest.mark.parametrize(
    ("row_type", "table", "extra"),
    [
        ("PositionFactRow", "bi_fact_position_daily", set()),
        ("PositionFactRow", "bi_fact_position_eom", set()),
        ("LoanEventFactRow", "bi_fact_loan_event", set()),
        ("EngineMetricFactRow", "bi_fact_engine_metric", set()),
    ],
)
def test_extract_rows_produce_every_mart_column(row_type: str, table: str, extra: set[str]) -> None:
    """RED until ``app.models.bi`` lands (Agent C1) — never skipped."""
    try:
        importlib.import_module("app.models.bi")
    except ModuleNotFoundError as exc:
        raise AssertionError("app.models.bi is not present yet") from exc
    row_fields = {f.name for f in fields(getattr(extract, row_type))}
    columns = set(Base.metadata.tables[table].columns.keys())
    assert row_fields == columns - BUILDER_STAMP - extra, (
        f"{row_type} vs {table}: missing {sorted(columns - BUILDER_STAMP - extra - row_fields)}, "
        f"extra {sorted(row_fields - columns)}"
    )


# --- purity of the domain package ----------------------------------------------------------


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_bi_domain_imports_no_service_model_or_sql() -> None:
    offenders: list[str] = []
    for path in sorted(BI_DOMAIN.rglob("*.py")):
        for name in _imports(path):
            if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"{path.relative_to(BACKEND)}: {name}")
    assert not offenders, "\n".join(offenders)


def test_bi_domain_uses_no_text_sql() -> None:
    for path in sorted(BI_DOMAIN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                assert name != "text", f"{path.relative_to(BACKEND)} calls text()"
