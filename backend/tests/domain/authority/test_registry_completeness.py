"""The Metric Authority Registry completeness gate (forensic re-audit D-9).

**What was wrong.** ``MetricAuthorityRegistry.register()`` is a dict insert that
raises on one thing: two literals in ``registry.py`` claiming the same
``AuthorityKey``. That is a constraint on the *catalogue*, not on the
*codebase*. So the registry could — and did — disagree with the repository in
every other direction at once: a metric computed and sealed into a filing run
with no authority behind it (D-8), a return declaring a ``methodology_id`` that
exists nowhere so its provenance shipped ``registry_status: not_registered``
(D-10), and no way for CI to notice either.

The one test that looked like a completeness check,
``test_registry.py::test_material_metric_is_registered``, is a hardcoded
28-item parametrize. It fails when a registration is REMOVED. It cannot fail
when a computation is ADDED — which is the only direction the defect travels.

**How this gate avoids the same rot.** Neither side is typed out here.

* The *computed* side is read out of the code by
  :func:`_persisted_metric_codes`: every ``RegulatoryMetricResult(...)``
  construction in ``app/``, resolved back to the string literal that names the
  metric. A new metric result is visible the moment it is written, and the
  extractor refuses to guess — if a persistence site stops matching the shape it
  can read, the gate FAILS naming that site rather than quietly seeing fewer
  metrics. A gate that silently narrows its own scope is worse than no gate.
* The *reporting* side is read off the live
  ``app.services.regulatory_reporting.registry.REGISTRY`` object, so every
  return — including the 24 workbook-driven ``bog_forms`` entries built at
  import time — is covered without anyone listing them.

The rules themselves are pure and live with the registry
(:meth:`app.domain.authority.registry.MetricAuthorityRegistry.check_completeness`);
this module supplies the evidence and asserts one rule per test, so a failure
names the invariant that broke rather than "the registry is bad".

**The three planes this gate could not see, and where they stand now**
(round 2, 2026-08-22). All three had their codes built inside engine functions
rather than named at the persistence site, which is why one extractor could not
reach them. :mod:`tests.domain.authority.plane_evidence` supplies three that can:

* ``RegulatoryLineItem`` — **covered.** Its NAMED vocabulary (``ratio:car``,
  ``fx_var:portfolio_var``, the SDI percentage-of-credit-RWA charges) is read out
  of the engines and must be claimed by an authority's ``line_item_codes``. Line
  codes keyed by the bank's own book — a GL category, a currency, a product, a
  tenor label — are *measured* and deliberately exempt: they are not a vocabulary
  a registry could enumerate, and a rule demanding one would be wrong the first
  time a tenant loaded a new product.
* ``live_metrics`` — **covered, at module granularity.** A per-key rule would be
  wrong (a live payload legitimately carries labels and echoed parameters beside
  its metrics) and a curated per-key allow-list would rot into a rubber stamp.
  What is not a judgment call is a module of which *not one* figure is registered
  and whose engine nothing names — a whole calculation engine absent from the
  registry, which is the largest form of the defect D-9 describes.
* the SDI read-side summaries — **measured, and governed elsewhere.**
  ``sdi_views._reserve_rows`` builds every figure through
  ``regulatory_parameters.resolve``, so each carries its own ``source_citation``
  and ``confirmation_status`` from the control plane. For a read-side view that
  is the stronger mechanism — the citation travels with the value instead of
  being looked up by name — so the honest finding is that this plane is not
  ungoverned, and the extractor records what it finds rather than asserting a
  count this registry was never going to own.

**Still not covered, stated so it is not mistaken for a clean bill.**
``RegulatoryValidation`` rule codes, ``run.metrics`` JSON keys that never become
a metric row (the capital ``stress_path`` and ``triggers`` blocks among them),
and every figure the BoG form templates compute in their own formula cells.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from app.domain.authority.registry import (
    CLASS_SPECIFIC_REGIMES,
    EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
    REGISTRY,
    AdvisoryDesignation,
    CodeEvidence,
    CompletenessFailure,
    InstitutionClass,
    MetricAuthority,
    MetricAuthorityRegistry,
    MetricFamily,
    Regime,
)
from app.services.regulatory_reporting.registry import REGISTRY as RETURN_REGISTRY

from .plane_evidence import (
    line_item_codes,
    live_metric_codes,
    sdi_read_side_codes,
)

#: The ORM class whose rows ARE the platform's filed metric figures: one row per
#: metric per sealed ``RegulatoryRun``, carrying the value, its regulatory
#: threshold and its compliance status. If a number reaches this table, the
#: platform computed it, stands behind it and files it — so it needs an
#: authority. This is the narrowest honest definition of "computed metric" and
#: the one the gate uses; see the module tail for what it deliberately excludes.
_PERSISTED_METRIC_MODEL = "RegulatoryMetricResult"

#: The keyword that names the metric on that constructor.
_METRIC_CODE_KEYWORD = "metric_code"

_APP_ROOT = pathlib.Path(__file__).resolve().parents[3] / "app"


# ---------------------------------------------------------------------------
# the computed side, read out of the code
# ---------------------------------------------------------------------------


def _row(node: ast.expr) -> Iterator[tuple[str, int]]:
    """One ``(code, value, unit, threshold, status)`` row, or an explanation."""
    if isinstance(node, ast.Tuple | ast.List) and node.elts:
        head = node.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            yield head.value, head.lineno
            return
    raise AssertionError(
        f"line {node.lineno}: a metric row does not name its metric with a string "
        "literal, so this completeness gate cannot see which metric is being persisted. "
        "Name it with a literal, or teach the extractor the new shape — do not leave a "
        "filed figure invisible to the authority gate."
    )


def _rows_in(node: ast.expr) -> Iterator[tuple[str, int]]:
    """The metric rows in an expression bound to a metric-row container.

    Deliberately NOT an ``ast.walk``: the value is either a container literal
    (every element a tuple/list, as in the assignment of ``metric_rows`` itself
    and in ``metric_rows.extend([...])``) or a single row (``.append((...))``).
    Descending further would sweep up tuples that are arguments to a status
    classifier and call them metrics.
    """
    if not isinstance(node, ast.Tuple | ast.List) or not node.elts:
        raise AssertionError(
            f"line {node.lineno}: a metric-row container is not a tuple/list literal, so "
            "the completeness gate cannot read the metrics this module files."
        )
    if all(isinstance(element, ast.Tuple | ast.List) for element in node.elts):
        for element in node.elts:
            yield from _row(element)
    else:
        yield from _row(node)


def _row_containers(tree: ast.Module, names: frozenset[str]) -> dict[str, list[tuple[str, int]]]:
    """Metric rows assigned, appended or extended into each named container."""
    containers: dict[str, list[tuple[str, int]]] = {name: [] for name in names}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    containers[target.id].extend(_rows_in(node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in names
            and node.value is not None
        ):
            containers[node.target.id].extend(_rows_in(node.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.args
        ):
            containers[node.func.value.id].extend(_rows_in(node.args[0]))
    return containers


def _enumerate_bindings(tree: ast.Module) -> dict[str, str]:
    """Loop variable -> the container it is the metric code of.

    Matches ``for position, (code, ...) in enumerate(metric_rows, start=1):`` —
    the shape every persistence site uses to turn rows into ORM instances.
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        iterated = node.iter
        if not (
            isinstance(iterated, ast.Call)
            and isinstance(iterated.func, ast.Name)
            and iterated.func.id == "enumerate"
            and iterated.args
            and isinstance(iterated.args[0], ast.Name)
        ):
            continue
        container = iterated.args[0].id
        target = node.target
        if not isinstance(target, ast.Tuple):
            continue
        for element in target.elts:
            if isinstance(element, ast.Tuple) and element.elts:
                head = element.elts[0]
                if isinstance(head, ast.Name):
                    bindings[head.id] = container
    return bindings


def _model_aliases(tree: ast.Module) -> frozenset[str]:
    """Every local name bound to the persisted-metric model in one module.

    ``from app.models import RegulatoryMetricResult as RMR`` followed by
    ``RMR(...)`` is the same persistence site under another name. Matching the
    bare class name only would have let a one-word rename take a filed metric
    out of the gate's sight without a single test going red.
    """
    aliases = {_PERSISTED_METRIC_MODEL}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name == _PERSISTED_METRIC_MODEL:
                    aliases.add(name.asname or name.name)
    return frozenset(aliases)


#: Persistence shapes that write rows without ever calling the ORM constructor.
#: The gate cannot read the metric codes out of them, so it must refuse rather
#: than report zero — a Core ``insert()`` is exactly how a filed figure would
#: leave the constructor site the extractor watches.
_UNREADABLE_WRITE_CALLS = frozenset(
    {"insert", "bulk_insert_mappings", "bulk_save_objects", "upsert", "values"}
)


def _refuse_unreadable_writes(
    path: pathlib.Path, tree: ast.Module, aliases: frozenset[str]
) -> None:
    """Fail loudly on a write path whose metric codes cannot be read.

    ``db.execute(insert(RegulatoryMetricResult), rows)`` persists filed metric
    results with no constructor and no literal in sight. Returning "no metrics
    here" for such a module is the placebo failure this gate exists to avoid.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name not in _UNREADABLE_WRITE_CALLS:
            continue
        targets = [*node.args, *(kw.value for kw in node.keywords)]
        if isinstance(func, ast.Attribute):
            targets.append(func.value)
        for target in targets:
            referenced = (isinstance(target, ast.Name) and target.id in aliases) or (
                isinstance(target, ast.Attribute) and target.attr == _PERSISTED_METRIC_MODEL
            )
            if referenced:
                raise AssertionError(
                    f"{path}:{node.lineno}: {name}() writes {_PERSISTED_METRIC_MODEL} rows "
                    "without constructing them, so the completeness gate cannot read which "
                    "metrics are filed here. Persist through the ORM constructor with a "
                    "literal metric_code, or teach the extractor this shape."
                )


def _codes_in_module(path: pathlib.Path) -> dict[str, int]:
    """Metric codes persisted by one module, or an assertion naming why not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _model_aliases(tree)
    _refuse_unreadable_writes(path, tree, aliases)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in aliases)
            # ``models.RegulatoryMetricResult(...)``: same construction, and the
            # ast.Name-only match used to read it as "this module files nothing".
            or (isinstance(node.func, ast.Attribute) and node.func.attr == _PERSISTED_METRIC_MODEL)
        )
    ]
    if not calls:
        return {}

    bindings = _enumerate_bindings(tree)
    containers = _row_containers(tree, frozenset(bindings.values()))
    found: dict[str, int] = {}
    for call in calls:
        keyword = next((kw for kw in call.keywords if kw.arg == _METRIC_CODE_KEYWORD), None)
        assert keyword is not None, (
            f"{path}:{call.lineno}: {_PERSISTED_METRIC_MODEL} is constructed without an "
            f"explicit {_METRIC_CODE_KEYWORD}=, so the authority gate cannot tell which "
            "metric this filed row carries."
        )
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.setdefault(str(value.value), value.lineno)
            continue
        assert isinstance(value, ast.Name), (
            f"{path}:{call.lineno}: {_METRIC_CODE_KEYWORD}= is a "
            f"{type(value).__name__} the completeness gate cannot resolve to a metric "
            "name. Persist metric codes as literals carried on rows, or teach the "
            "extractor this shape — an unreadable site is an unguarded filed figure."
        )
        container = bindings.get(value.id)
        assert container is not None and containers.get(container), (
            f"{path}:{call.lineno}: {_METRIC_CODE_KEYWORD}={value.id!r} is not bound from "
            "a row container this gate can read, so the metrics this module files are "
            "invisible to the authority registry."
        )
        for code, lineno in containers[container]:
            found.setdefault(code, lineno)

    assert found, (
        f"{path}: constructs {_PERSISTED_METRIC_MODEL} but the gate extracted no metric "
        "codes. The persistence shape changed and the completeness gate went blind."
    )
    return found


def _persisted_metric_codes() -> dict[str, str]:
    """``metric_id -> file:line`` for every filed metric result written in ``app/``."""
    sites: dict[str, str] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        # Deliberately NOT ``f"{model}("``: that pre-filter skipped the whole file
        # when the class was imported under an alias, so a rename at the import
        # line silently removed a persistence site from the gate's scope.
        if _PERSISTED_METRIC_MODEL not in path.read_text(encoding="utf-8"):
            continue
        for code, lineno in _codes_in_module(path).items():
            sites.setdefault(code, f"{path.relative_to(_APP_ROOT.parent)}:{lineno}")
    return sites


# ---------------------------------------------------------------------------
# the reporting side, read off the live return registry
# ---------------------------------------------------------------------------


def _reporting_references() -> dict[tuple[str, str], str]:
    references: dict[tuple[str, str], str] = {}
    for code, definition in sorted(RETURN_REGISTRY.items()):
        declared: tuple[tuple[str, str], ...] = (
            getattr(definition, "declared_methodologies", ()) or ()
        )
        for metric_id, methodology_id in declared:
            references[metric_id, methodology_id] = (
                f"return {code} (declared_methodologies, "
                "app/services/regulatory_reporting/registry.py)"
            )
    return references


def _return_definitions() -> dict[str, Any]:
    return dict(RETURN_REGISTRY)


#: Methodologies that FILE a figure while their governance still carries
#: :data:`EXTERNAL_REGULATORY_VERIFICATION_REQUIRED`. Being on this list is a
#: DISCLOSURE, not an authority and not a waiver — the figure is still filed on
#: a basis this repository cannot establish, and the reason says which part is
#: missing. The gate fails on any methodology that starts filing on the sentinel
#: without an entry here, and equally on an entry whose sentinel has since been
#: resolved, so the register can only move deliberately and in one direction.
_ACKNOWLEDGED_PENDING_FILED: dict[str, str] = {
    "basel_irrbb_run": (
        "The Basel IRRBB standard is established, and the BoG shock set is now located "
        "too - Guideline on the Management and Measurement of Interest Rate Risk in the "
        "Banking Book, 2026, Appendix II P1 and Table 5, Ghana cedi 450 basis points "
        "mandatory (docs/bog_parameter_sources.md). The sentinel stays because that "
        "guideline is an EXPOSURE DRAFT: issued February 2026, effective 1 January 2027 "
        "at P9, comment window closed 30 June 2026 with no final version published. So "
        "the shock set is sourced but not in force, which is a different open question "
        "from the one this reason used to record."
    ),
    "bog_fx_nop_run": (
        "authority_reference is the bare sentinel: no BoG instrument setting the net open "
        "position limits is established in this repository, and none was invented "
        "(registry.py::_fx). The engine and its parameters are governed; the legal basis "
        "for the limits is not."
    ),
    # bog_five_grade_classification left this register in credit PR-6: the
    # credit run's ENGINE_VERSION now governs the figure, so the sentinel — and
    # its acknowledgement — are gone together.
}


def _live_module_metrics() -> dict[str, dict[str, str]]:
    """``module -> {live metric key: file:line}``, grouped from the flat read."""
    grouped: dict[str, dict[str, str]] = {}
    for code, site in live_metric_codes().items():
        module = site.split(":", 1)[0]
        grouped.setdefault(module, {})[code] = site
    return grouped


#: Methodologies designated FILED on an instrument that is **published but not
#: commenced**. A third state from the sentinel register above: these carry real
#: paragraph numbers, so ``requires_external_verification`` is False and nothing
#: else in the gate can see them. Being here is a DISCLOSURE that filing ahead of
#: commencement is intended, never a finding that the instrument is in force.
_ACKNOWLEDGED_FILED_ON_DRAFT: dict[str, str] = {
    "lmtd_table1_ratio": (
        "The Liquidity Monitoring Tools Directive was posted as an EXPOSURE DRAFT on "
        "19 February 2026, effective 1 January 2027, comment window closed 30 June 2026 "
        "with no final version published (docs/bog_parameter_sources.md). Building the "
        "eight Table-1 ratios ahead of commencement is deliberate - a bank has to be "
        "able to file on day one - and the per-class floors already fail closed rather "
        "than substituting a bank floor for an SDI. What must not happen silently is a "
        "figure presented as a settled regulatory floor before the directive is law."
    ),
    "lmtd_table11_capped": (
        "Same instrument and same exposure-draft status as lmtd_table1_ratio, at "
        "paragraphs 39-43 (Table 11). This entry additionally caps inflows at a "
        "HARD-CODED 0.75 (le_generation._LCR_INFLOW_CAP) rather than a governed "
        "parameter, so the cap would not move if the final directive changed it."
    ),
    "basel_irrbb_run": (
        "The BoG Guideline on the Management and Measurement of Interest Rate Risk in "
        "the Banking Book, 2026 is an EXPOSURE DRAFT: issued February 2026 under Act 930 "
        "s.92(1), effective 1 January 2027 at paragraph 9, with a one-year pilot. The "
        "Basel IRRBB standard behind the mechanics is settled; the Ghanaian instrument "
        "that would make these figures a filing obligation is not yet law."
    ),
}


@pytest.fixture(scope="module")
def evidence() -> CodeEvidence:
    definitions = _return_definitions()
    named_lines, data_keyed_lines = line_item_codes()
    return CodeEvidence(
        computed_metrics=_persisted_metric_codes(),
        reporting_references=_reporting_references(),
        return_codes=frozenset(definitions),
        return_families=frozenset(
            definition.family for definition in definitions.values() if definition.family
        ),
        acknowledged_pending_filed=_ACKNOWLEDGED_PENDING_FILED,
        filed_line_item_codes=named_lines,
        data_keyed_line_item_codes=data_keyed_lines,
        live_module_metrics=_live_module_metrics(),
        acknowledged_filed_on_draft=_ACKNOWLEDGED_FILED_ON_DRAFT,
        sdi_read_side_metrics=sdi_read_side_codes(),
    )


@pytest.fixture(scope="module")
def failures(evidence: CodeEvidence) -> tuple[CompletenessFailure, ...]:
    return REGISTRY.check_completeness(evidence)


def _of(failures: tuple[CompletenessFailure, ...], rule: str) -> list[str]:
    return [str(failure) for failure in failures if failure.rule == rule]


# ---------------------------------------------------------------------------
# the extractor must be able to see, before anything it says can be trusted
# ---------------------------------------------------------------------------


def test_the_gate_can_still_read_every_metric_persistence_site() -> None:
    """A gate that silently sees nothing is worse than no gate at all.

    ``_persisted_metric_codes`` raises rather than shrinks when a persistence
    site changes shape, so the only remaining failure mode is the whole scan
    finding nothing — which this pins.
    """
    codes = _persisted_metric_codes()
    assert len(codes) > 30, (
        "the completeness gate extracted almost no metric codes from app/ — the "
        f"{_PERSISTED_METRIC_MODEL} persistence shape has changed and every rule below "
        f"is now vacuous. Extracted: {sorted(codes)}"
    )
    # A control: the flagship filed ratios must be visible to the extractor, or
    # the gate is reading something other than the filed plane.
    assert {"car_pct", "lcr_pct", "nsfr_pct", "tier1_ratio_pct"} <= set(codes)


def test_the_reporting_side_is_read_from_the_live_return_registry() -> None:
    definitions = _return_definitions()
    assert len(definitions) > 30, (
        "the return registry produced almost no definitions; the reporting half of the "
        "gate would pass vacuously"
    )
    references = _reporting_references()
    assert references, (
        "no return declares a methodology at all — CF-1's 'which lcr_pct does this "
        "surface mean' would be unanswered everywhere and this gate would prove nothing"
    )


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def test_every_persisted_regulatory_metric_has_a_registered_authority(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """A figure the platform seals into a filing run must have a declared owner.

    This is the rule D-8 slipped past: four unregistered computation sites,
    invisible to CI because nothing joined the engines to the registry.

    Do NOT make this pass by adding registry entries. An entry asserts a legal
    basis; if the basis does not exist, the honest fix is that the engine stops
    producing a filed figure, not that the catalogue grows a row to match it.
    """
    unbacked = _of(failures, "unbacked_computed_metric")
    assert not unbacked, "\n".join(["metrics filed with no registered authority:", *unbacked])


def test_no_return_declares_a_methodology_that_resolves_nowhere(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """Forensic re-audit D-10, as a rule rather than an instance.

    ``LCR-NSFR`` declared ``basel_bog_bsd3`` against a registry that knows
    ``basel_bog_liquidity_run``. Two green tests pinned the typo because both
    compared a literal to a literal; neither asked the registry. This asks the
    registry, for every return, including the workbook-driven BoG forms.
    """
    dangling = _of(failures, "dangling_methodology_reference")
    assert not dangling, "\n".join(["declared methodologies that resolve nowhere:", *dangling])


def test_every_authority_answers_its_required_governance_fields(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """Blank is not an answer; the sentinel is.

    ``authority_reference`` — what the rest of the platform calls
    ``source_citation`` — is the field that says under what law the figure
    exists. An entry that leaves it blank looks registered and backs nothing.
    """
    incomplete = _of(failures, "missing_required_field")
    assert not incomplete, "\n".join(["authorities with unanswered fields:", *incomplete])


def test_the_bank_and_sdi_regimes_never_share_or_inherit_an_authority(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """``crd`` and ``s29`` are different statutes, not two flavours of one.

    Three ways they leak, all checked: an entry filed under one regime while
    declaring the other's class (or ``ALL``, which
    ``for_institution_class`` hands to BOTH); one ``methodology_id`` claimed by
    two regimes; and one citation backing entries in both, which is an
    inherited authority however it got there.
    """
    breaches = [
        *_of(failures, "regime_class_mismatch"),
        *_of(failures, "methodology_shared_across_regimes"),
        *_of(failures, "citation_shared_across_regimes"),
    ]
    assert not breaches, "\n".join(["regime boundary breaches:", *breaches])


def test_no_authority_claims_a_return_that_does_not_exist(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """``reporting_mappings`` and ``return_family`` rot exactly like a
    methodology id does — silently, and in the direction of claiming more."""
    dangling = [
        *_of(failures, "dangling_reporting_mapping"),
        *_of(failures, "dangling_return_family"),
    ]
    assert not dangling, "\n".join(["authorities pointing at returns that are gone:", *dangling])


def test_a_filed_figure_on_an_unverified_citation_is_never_silent(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """ "No authority" and "authority pending verification" are different states.

    The gate must not conflate them, and must not let the second one back a
    filed number quietly. Every methodology that files on the sentinel is named
    with the reason; a new one fails here, and so does a stale acknowledgement
    left behind after a citation is finally established.
    """
    unacknowledged = [
        *_of(failures, "unacknowledged_pending_filed"),
        *_of(failures, "stale_pending_acknowledgement"),
    ]
    assert not unacknowledged, "\n".join(
        ["filed figures standing on an unverified citation:", *unacknowledged]
    )


def test_every_named_line_item_a_sealed_run_files_has_an_authority(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """The other half of what a filing run persists.

    ``RegulatoryMetricResult`` was the whole of the first gate's scope, and it is
    roughly half the figures a sealed run writes. The line-item half carries
    numbers no metric row does — the operational-risk BIA charge, the FX VaR
    decomposition, the SDI's prescribed percentage-of-credit-RWA charges — and
    their codes are chosen inside the engines, which is why they were invisible.

    Same standing instruction as the metric rule: do NOT clear this by inventing
    entries. Declaring a line code on an authority that already covers the engine
    asserts nothing new; declaring one nothing covers would.
    """
    unbacked = _of(failures, "unbacked_filed_line_item")
    assert not unbacked, "\n".join(["named line items filed with no authority:", *unbacked])


def test_every_module_publishing_live_figures_is_in_the_registry(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """A whole calculation engine absent from the registry.

    The live plane is not filed, so a per-key rule would be wrong — a live
    payload legitimately carries labels, scenario codes and echoed parameters
    beside its metrics. What is not a judgment call is whether the module
    computing them is in the registry at all.
    """
    orphans = _of(failures, "unregistered_calculation_module")
    assert not orphans, "\n".join(
        ["calculation modules publishing live figures with no registered authority:", *orphans]
    )


def test_a_filed_figure_on_an_uncommenced_instrument_is_never_silent(
    failures: tuple[CompletenessFailure, ...],
) -> None:
    """ "No citation", "citation pending verification" and "citation not yet law"
    are three different states, and the registry could only express two.

    The nine Liquidity Monitoring Tools Directive entries cite ``paragraph 9`` of
    a document posted as an exposure draft on 19 February 2026 and effective
    1 January 2027. Because the paragraph is real,
    ``requires_external_verification`` is False and the sentinel register looked
    past them entirely. This rule is what sees them.
    """
    undisclosed = [
        *_of(failures, "undisclosed_draft_instrument"),
        *_of(failures, "stale_draft_acknowledgement"),
    ]
    assert not undisclosed, "\n".join(
        ["filed figures standing on an instrument that has not commenced:", *undisclosed]
    )


def test_the_lmtd_class_neutrality_is_a_fact_about_the_directive() -> None:
    """``CLASS_SPECIFIC_REGIMES`` constrains ``crd`` and ``s29`` and NOT ``lmtd``.

    That looked like an omission and is not. The Capital Requirements Directive
    excludes SDIs at its own paragraph 2 and Act 930 s.29 is the SDI statute, so
    those two regimes bind exactly one class each and an ``ALL`` entry under
    either is an inherited authority. The Liquidity Monitoring Tools Directive
    binds BOTH classes with ONE formula and TWO floor sets, and the platform
    already models that correctly: the floors are per-class control-plane
    parameters (``regulatory_parameters._LMTD_FLOORS``) and
    ``liquidity_thresholds`` fails closed with ``sdi_liquidity_floor_unseeded``
    rather than showing a bank floor to an SDI.

    So ``ALL`` on these entries is the accurate statement, and splitting them
    into a bank entry and an SDI entry would assert two authorities where the
    directive creates one. What was genuinely undisclosed is that the directive
    is not yet law, and that is the rule above, not this one.
    """
    lmtd = REGISTRY.for_regime(Regime.LMTD)
    assert lmtd, "the LMTD entries vanished; this reasoning no longer describes anything"
    assert all(entry.institution_class is InstitutionClass.ALL for entry in lmtd)
    assert Regime.LMTD not in CLASS_SPECIFIC_REGIMES
    assert all(not entry.instrument_in_force for entry in lmtd), (
        "every LMTD entry cites an exposure draft; if one now claims its instrument is "
        "in force, the directive was finalised and this whole register needs revisiting"
    )


# ---------------------------------------------------------------------------
# the three extra planes must be visible before their rules mean anything
# ---------------------------------------------------------------------------


def test_the_line_item_plane_is_read_and_its_boundary_is_measured() -> None:
    named, data_keyed = line_item_codes()
    assert len(named) > 15, (
        "the named line-item vocabulary collapsed; the line-item rule would pass "
        f"vacuously. Extracted: {sorted(named)}"
    )
    assert data_keyed, (
        "no data-keyed line code was found, which cannot be true — HQLA lines, FX "
        "positions and FTP products are all keyed by the bank's own book. The "
        "extractor has stopped distinguishing the enumerable vocabulary from the "
        "non-enumerable one, and the named set can no longer be trusted"
    )
    # Controls: one line whose code is the platform's choice, one that is the
    # bank's data. If these ever swap sides the classification has broken.
    assert "ratio:car" in named
    assert any(key.startswith("fx_position:") for key in data_keyed)


def test_the_live_plane_is_read_from_every_module_that_publishes_one() -> None:
    grouped = _live_module_metrics()
    assert len(grouped) >= 6, (
        f"only {len(grouped)} modules publish live metrics? every regulatory module "
        "defines compute_live; the extractor has gone blind"
    )
    codes = live_metric_codes()
    assert len(codes) > 40, f"live plane extraction collapsed to {len(codes)} keys"
    assert {"car_pct", "lcr_pct", "nsfr_pct"} <= set(codes)


def test_the_sdi_read_side_extractor_reports_what_it_finds() -> None:
    """Measured, not assumed: the SDI summaries carry their OWN provenance.

    ``sdi_views._reserve_rows`` builds each figure through
    ``regulatory_parameters.resolve``, so every SDI read-side number is served
    with its own ``source_citation`` and ``confirmation_status`` from the control
    plane. That is a different governance mechanism from this registry, and a
    stronger one for a read-side view — the citation travels with the value
    instead of being looked up by name.

    So the honest finding for this plane is that it is governed elsewhere, and
    this test records the measurement rather than asserting a count the registry
    was never going to own.
    """
    codes = sdi_read_side_codes()
    assert isinstance(codes, dict)
    for code, site in codes.items():
        assert code and site.startswith("app/services/sdi_")


def test_the_pending_register_records_a_reason_not_a_waiver() -> None:
    """An acknowledgement with no reason is a waiver wearing a disclosure's hat."""
    for methodology_id, reason in _ACKNOWLEDGED_PENDING_FILED.items():
        assert len(reason.split()) >= 15, (
            f"{methodology_id}: an acknowledged pending-verification methodology must say "
            "WHICH part of its basis is unestablished, in enough words to act on"
        )
    filed_pending = {
        entry.methodology_id
        for entry in REGISTRY
        if entry.advisory_designation is AdvisoryDesignation.FILED
        and entry.requires_external_verification
    }
    assert set(_ACKNOWLEDGED_PENDING_FILED) == filed_pending, (
        "the acknowledged register and the measured set of filed-on-sentinel "
        "methodologies must be the same set"
    )


# ---------------------------------------------------------------------------
# the rules must actually fire — a gate whose rules cannot fail guards nothing
# ---------------------------------------------------------------------------


def _fixture(**overrides: object) -> MetricAuthority:
    base: dict[str, object] = {
        "metric_id": "test_metric",
        "metric_family": MetricFamily.CAPITAL,
        "institution_class": InstitutionClass.BANK,
        "jurisdiction": "GH",
        "regulator": "BOG",
        "regime": Regime.CRD_BASEL,
        "methodology_id": "test_methodology",
        "return_family": "capital",
        "effective_from": date(2026, 1, 1),
        "policy_resolver": "app.services.params:get_active_params",
        "calculation_engine": "app.domain.capital.engine:compute_rwa",
        "calculation_version": "regulatory-capital-v2.0.0",
        "authority_reference": "BoG Capital Requirements Directive",
    }
    base.update(overrides)
    return MetricAuthority(**base)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> CodeEvidence:
    base: dict[str, object] = {
        "computed_metrics": {},
        "reporting_references": {},
        "return_codes": frozenset({"CAR-RWA"}),
        "return_families": frozenset({"capital"}),
        "acknowledged_pending_filed": {},
    }
    base.update(overrides)
    return CodeEvidence(**base)  # type: ignore[arg-type]


def _rules(registry: MetricAuthorityRegistry, evidence: CodeEvidence) -> set[str]:
    return {failure.rule for failure in registry.check_completeness(evidence)}


def test_a_complete_coherent_registry_produces_no_failures() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(reporting_mappings=("CAR-RWA",)))
    evidence = _evidence(
        computed_metrics={"test_metric": "app/services/x.py:1"},
        reporting_references={("test_metric", "test_methodology"): "return CAR-RWA"},
    )
    assert registry.check_completeness(evidence) == ()


def test_a_computed_metric_with_no_authority_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture())
    failures = registry.check_completeness(
        _evidence(computed_metrics={"orphan_pct": "app/services/x.py:42"})
    )
    assert [f.rule for f in failures] == ["unbacked_computed_metric"]
    assert "app/services/x.py:42" in failures[0].message


def test_a_metric_registered_under_another_methodology_still_counts_as_backed() -> None:
    """The rule asks whether the METRIC has an owner, not which one computed it.

    Which methodology a given surface means is the reporting rule's job; this
    rule is only about a figure nobody owns at all.
    """
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(methodology_id="some_other_method"))
    assert "unbacked_computed_metric" not in _rules(
        registry, _evidence(computed_metrics={"test_metric": "app/services/x.py:1"})
    )


def test_a_declared_methodology_that_resolves_nowhere_fails() -> None:
    """The D-10 class: ``('lcr_pct', 'basel_bog_bsd3')`` against a registry that
    knows ``basel_bog_liquidity_run``."""
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(metric_id="lcr_pct", methodology_id="basel_bog_liquidity_run"))
    failures = registry.check_completeness(
        _evidence(reporting_references={("lcr_pct", "basel_bog_bsd3"): "return LCR-NSFR"})
    )
    assert [f.rule for f in failures] == ["dangling_methodology_reference"]
    assert "basel_bog_liquidity_run" in failures[0].message, (
        "the failure must name the methodology that DOES exist, or the reader "
        "cannot tell a typo from a genuinely missing registration"
    )


def test_a_blank_citation_fails_but_the_sentinel_does_not() -> None:
    blank = MetricAuthorityRegistry()
    blank.register(_fixture(authority_reference=""))
    assert "missing_required_field" in _rules(blank, _evidence())

    honest = MetricAuthorityRegistry()
    honest.register(_fixture(authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED))
    assert "missing_required_field" not in _rules(honest, _evidence())


def test_source_citation_is_the_same_field_as_authority_reference() -> None:
    entry = _fixture(authority_reference="BoG CRD paragraph 73(b)")
    assert entry.source_citation == entry.authority_reference


@pytest.mark.parametrize(
    ("regime", "institution_class"),
    [
        (Regime.CRD_BASEL, InstitutionClass.SDI),
        (Regime.CRD_BASEL, InstitutionClass.ALL),
        (Regime.ACT930_S29, InstitutionClass.BANK),
        (Regime.ACT930_S29, InstitutionClass.ALL),
    ],
)
def test_a_class_specific_regime_rejects_the_wrong_class(
    regime: Regime, institution_class: InstitutionClass
) -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(regime=regime, institution_class=institution_class))
    assert "regime_class_mismatch" in _rules(registry, _evidence())


def test_one_methodology_claimed_by_two_regimes_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(regime=Regime.CRD_BASEL, institution_class=InstitutionClass.BANK))
    registry.register(
        _fixture(
            regime=Regime.ACT930_S29,
            institution_class=InstitutionClass.SDI,
            authority_reference="Act 930 s.29",
        )
    )
    assert "methodology_shared_across_regimes" in _rules(registry, _evidence())


def test_one_citation_backing_both_regimes_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(regime=Regime.CRD_BASEL, institution_class=InstitutionClass.BANK))
    registry.register(
        _fixture(
            regime=Regime.ACT930_S29,
            institution_class=InstitutionClass.SDI,
            methodology_id="s29_method",
        )
    )
    assert "citation_shared_across_regimes" in _rules(registry, _evidence())


def test_the_same_engine_serving_both_regimes_is_allowed_when_the_law_is_not_shared() -> None:
    """``classify_book`` computes both the BoG five-grade and the NBFI four-grade
    provision books. One pure function, two statutes, two methodologies, two
    citations — that is correct, and the gate must not punish it."""
    registry = MetricAuthorityRegistry()
    engine = "app.domain.capital.loan_classification:classify_book"
    registry.register(
        _fixture(
            metric_id="npl_ratio",
            methodology_id="bog_five_grade_classification",
            calculation_engine=engine,
        )
    )
    registry.register(
        _fixture(
            metric_id="npl_ratio",
            regime=Regime.ACT930_S29,
            institution_class=InstitutionClass.SDI,
            methodology_id="nbfi_four_grade_classification",
            calculation_engine=engine,
            authority_reference="NBFI Business Rules 2000, rules 17-19",
        )
    )
    assert not _rules(registry, _evidence())


def test_an_authority_pointing_at_a_return_that_does_not_exist_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(reporting_mappings=("BSD-GONE!E70",), return_family="ghosts"))
    assert _rules(registry, _evidence()) == {
        "dangling_reporting_mapping",
        "dangling_return_family",
    }


def test_a_cell_level_mapping_resolves_on_its_return_code() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(reporting_mappings=("CAR-RWA!E70",)))
    assert not _rules(registry, _evidence())


def test_filing_on_an_unverified_citation_fails_until_it_is_acknowledged() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(
        _fixture(
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            reporting_mappings=("CAR-RWA",),
        )
    )
    assert "unacknowledged_pending_filed" in _rules(registry, _evidence())
    assert "unacknowledged_pending_filed" not in _rules(
        registry, _evidence(acknowledged_pending_filed={"test_methodology": "why"})
    )


def test_an_unverified_citation_that_is_never_filed_needs_no_acknowledgement() -> None:
    """The distinction the gate exists to preserve: pending verification is only
    a build failure when a FILED number stands on it."""
    registry = MetricAuthorityRegistry()
    registry.register(
        _fixture(
            advisory_designation=AdvisoryDesignation.ADVISORY_ONLY,
            authority_reference=EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
            reporting_mappings=(),
        )
    )
    assert not _rules(registry, _evidence())


def test_a_stale_acknowledgement_fails_once_the_citation_is_established() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(advisory_designation=AdvisoryDesignation.FILED))
    assert "stale_pending_acknowledgement" in _rules(
        registry, _evidence(acknowledged_pending_filed={"test_methodology": "resolved long ago"})
    )


def test_filing_on_an_uncommenced_instrument_fails_until_it_is_acknowledged() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(
        _fixture(
            advisory_designation=AdvisoryDesignation.FILED,
            authority_reference="BoG Some Directive 2026, paragraph 9",
            instrument_in_force=False,
            reporting_mappings=("CAR-RWA",),
        )
    )
    assert "undisclosed_draft_instrument" in _rules(registry, _evidence())
    assert "undisclosed_draft_instrument" not in _rules(
        registry, _evidence(acknowledged_filed_on_draft={"test_methodology": "why"})
    )


def test_a_draft_acknowledgement_goes_stale_when_the_instrument_commences() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(advisory_designation=AdvisoryDesignation.FILED))
    assert "stale_draft_acknowledgement" in _rules(
        registry, _evidence(acknowledged_filed_on_draft={"test_methodology": "commenced already"})
    )


def test_an_uncommenced_instrument_that_is_never_filed_needs_no_disclosure() -> None:
    """The four February 2026 drafts also back supervisory-monitoring figures.
    Reading a draft for an internal view is not the same act as filing on it."""
    registry = MetricAuthorityRegistry()
    registry.register(
        _fixture(
            advisory_designation=AdvisoryDesignation.SUPERVISORY_MONITORING,
            instrument_in_force=False,
            reporting_mappings=(),
        )
    )
    assert not _rules(registry, _evidence())


def test_a_named_line_item_no_authority_claims_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(line_item_codes=("ratio:car",)))
    failures = registry.check_completeness(
        _evidence(
            filed_line_item_codes={
                "ratio:car": "app/domain/capital/engine.py:911",
                "fx_var:diversification_benefit": "app/domain/fx/engine.py:340",
            }
        )
    )
    assert [f.rule for f in failures] == ["unbacked_filed_line_item"]
    assert "diversification_benefit" in failures[0].subject


def test_a_data_keyed_line_code_is_measured_and_never_demanded() -> None:
    """``fx_position:position.currency`` is the bank's book, not a vocabulary.

    A registry that demanded an authority per currency, per GL category and per
    product would be wrong the first time a tenant loaded a new product. The
    boundary is recorded in the evidence so it is measured rather than assumed,
    and no rule fires on it.
    """
    registry = MetricAuthorityRegistry()
    registry.register(_fixture())
    assert not registry.check_completeness(
        _evidence(
            data_keyed_line_item_codes={
                "fx_position:position.currency": "app/domain/fx/engine.py:264",
                "ftp_product:product.product": "app/domain/ftp/engine.py:382",
            }
        )
    )


def test_a_module_with_no_registered_metric_at_all_fails() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(metric_id="car_pct"))
    failures = registry.check_completeness(
        _evidence(
            live_module_metrics={
                "app/services/rating.py": {"pit_rating_grade": "app/services/rating.py:10"}
            }
        )
    )
    assert [f.rule for f in failures] == ["unregistered_calculation_module"]
    assert "pit_rating_grade" in failures[0].message


def test_a_live_payload_may_carry_labels_beside_its_registered_metrics() -> None:
    """The rule that would have been wrong: demanding an authority per live key.

    ``worst_scenario`` is a scenario code, ``single_ccy_max_currency`` a currency
    label, ``eve_limit_pct`` an echoed governed parameter. None is a computed
    regulatory figure, and a per-key allow-list curating them would rot into a
    rubber stamp. A module carrying at least one registered metric is in the
    registry; that is the honest boundary.
    """
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(metric_id="car_pct"))
    assert not _rules(
        registry,
        _evidence(
            live_module_metrics={
                "app/services/regulatory_capital.py": {
                    "car_pct": "app/services/regulatory_capital.py:1290",
                    "worst_scenario": "app/services/regulatory_capital.py:1290",
                }
            }
        ),
    )


def test_a_module_is_covered_when_an_authority_names_its_engine() -> None:
    """Live keys and metric ids need not share names — the FX live view calls
    Tier 1 ``tier1_ghs`` while the registry calls it ``tier1_capital``. Naming
    the module as a ``calculation_engine`` is the other way to be registered."""
    registry = MetricAuthorityRegistry()
    registry.register(_fixture(calculation_engine="app.services.rating:compute_live"))
    assert not _rules(
        registry,
        _evidence(
            live_module_metrics={
                "app/services/rating.py": {"pit_rating_grade": "app/services/rating.py:10"}
            }
        ),
    )


# ---------------------------------------------------------------------------
# the extractor must refuse to guess
# ---------------------------------------------------------------------------


_READABLE_SITE = """
def persist(db, run):
    metric_rows = (
        ("car_pct", ratios.car_pct, "pct", params.car_min_pct, ratios.car_status),
        ("nsfr_pct", nsfr.nsfr_pct, "pct", None, "na"),
    )
    if stress is not None:
        metric_rows.append(("car_pct_end", end.car, "pct", None, "na"))
    for position, (code, value, unit, threshold_min, status) in enumerate(metric_rows, start=1):
        db.add(RegulatoryMetricResult(metric_code=code, position=position))
"""


def test_the_extractor_reads_a_conventional_persistence_site(tmp_path: pathlib.Path) -> None:
    site = tmp_path / "site.py"
    site.write_text(_READABLE_SITE, encoding="utf-8")
    assert set(_codes_in_module(site)) == {"car_pct", "nsfr_pct", "car_pct_end"}


def test_the_extractor_refuses_a_computed_metric_code(tmp_path: pathlib.Path) -> None:
    """The failure mode that would make this gate a placebo: a metric name the
    extractor cannot read, silently dropping a filed figure out of scope."""
    site = tmp_path / "site.py"
    site.write_text(
        "def persist(db, run):\n"
        "    db.add(RegulatoryMetricResult(metric_code=f'car_{suffix}', position=1))\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="cannot resolve to a metric name"):
        _codes_in_module(site)


def test_the_extractor_refuses_a_row_that_does_not_name_its_metric(
    tmp_path: pathlib.Path,
) -> None:
    site = tmp_path / "site.py"
    site.write_text(
        "def persist(db, run):\n"
        "    metric_rows = ((code_for(x), 1, 'pct', None, 'na'),)\n"
        "    for position, (code, value, unit, low, status) in enumerate(metric_rows, start=1):\n"
        "        db.add(RegulatoryMetricResult(metric_code=code, position=position))\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="does not name its metric with a string literal"):
        _codes_in_module(site)


def test_the_extractor_reads_an_aliased_import(tmp_path: pathlib.Path) -> None:
    """Evasion found auditing this gate: ``import ... as RMR`` and the whole file
    fell out of scope, because the scan pre-filtered on the literal text
    ``RegulatoryMetricResult(``. No test went red; the metric simply vanished."""
    site = tmp_path / "site.py"
    site.write_text(
        "from app.models import RegulatoryMetricResult as RMR\n"
        "def persist(db, run):\n"
        "    db.add(RMR(metric_code='car_pct', position=1))\n",
        encoding="utf-8",
    )
    assert _PERSISTED_METRIC_MODEL in site.read_text(encoding="utf-8"), (
        "the file-level pre-filter must key on the class NAME, not on the name "
        "followed by a bracket, or an aliased import is invisible before parsing"
    )
    assert set(_codes_in_module(site)) == {"car_pct"}


def test_the_extractor_reads_an_attribute_construction(tmp_path: pathlib.Path) -> None:
    """Second evasion: ``models.RegulatoryMetricResult(...)`` matched no
    ``ast.Name``, so the extractor returned an empty dict and reported the module
    as filing nothing — a silent narrowing, which is the one failure mode this
    gate's docstring promises it does not have."""
    site = tmp_path / "site.py"
    site.write_text(
        "from app import models\n"
        "def persist(db, run):\n"
        "    db.add(models.RegulatoryMetricResult(metric_code='lcr_pct', position=1))\n",
        encoding="utf-8",
    )
    assert set(_codes_in_module(site)) == {"lcr_pct"}


def test_the_extractor_refuses_a_core_insert_it_cannot_read(tmp_path: pathlib.Path) -> None:
    """Third evasion: a Core ``insert()`` persists filed metric rows with no
    constructor and no literal. Reporting zero metrics for such a module would
    make the gate a placebo exactly where it matters."""
    site = tmp_path / "site.py"
    site.write_text(
        "from sqlalchemy import insert\n"
        "from app.models import RegulatoryMetricResult\n"
        "def persist(db, rows):\n"
        "    db.execute(insert(RegulatoryMetricResult), rows)\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="without constructing them"):
        _codes_in_module(site)


def test_a_read_only_importer_is_not_mistaken_for_a_write_path(tmp_path: pathlib.Path) -> None:
    """``select(RegulatoryMetricResult)`` is how several services READ filed
    metrics back. The refusal above must not fire on them, or the gate becomes
    noise and gets deleted."""
    site = tmp_path / "site.py"
    site.write_text(
        "from sqlalchemy import select\n"
        "from app.models import RegulatoryMetricResult\n"
        "def read(db, run):\n"
        "    return db.scalars(\n"
        "        select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == run.id)\n"
        "    ).all()\n",
        encoding="utf-8",
    )
    assert _codes_in_module(site) == {}


def test_the_extractor_reads_a_literal_metric_code(tmp_path: pathlib.Path) -> None:
    site = tmp_path / "site.py"
    site.write_text(
        "def persist(db, run):\n"
        "    db.add(RegulatoryMetricResult(metric_code='car_pct', position=1))\n",
        encoding="utf-8",
    )
    assert set(_codes_in_module(site)) == {"car_pct"}
