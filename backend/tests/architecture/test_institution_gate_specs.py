"""The simple institution gates keep one public shape and one policy sentence each.

Two pins, both readable without Postgres:

* the **policy snapshot**: the complete sentence each gate hands to the shared
  institution check (module, sensitivity, permission, surface, denial detail and
  the irregular flags), captured by recording the arguments the gate passes on;
* the **public shape**: each gate stays its own function with the name, qualname,
  signature (annotations included) and docstring it has today, because route
  wiring, the route catalogue labels and the examiner contract all key on them.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from app.api import deps

#: Every simple gate, in the order the deps module defines it.
GATE_NAMES: tuple[str, ...] = (
    "require_capital_aggregated_view",
    "require_capital_confidential_view",
    "require_capital_restricted_view",
    "require_capital_run",
    "require_credit_aggregated_view",
    "require_credit_concentration_view",
    "require_credit_restricted_view",
    "require_credit_blotter_view",
    "require_credit_activity_view",
    "require_credit_run",
    "require_fx_aggregated_view",
    "require_fx_run",
    "require_forecasting_aggregated_view",
    "require_forecasting_confidential_view",
    "require_forecasting_run_detail_view",
    "require_forecasting_run",
    "require_forecasting_assumption_edit",
    "require_forecasting_assumption_review",
    "require_markets_published_view",
    "require_markets_template_view",
    "require_markets_confidential_view",
    "require_markets_restricted_view",
    "require_markets_run",
    "require_markets_upload",
    "require_markets_overlay_create",
    "require_markets_overlay_edit",
)


@dataclass(frozen=True)
class GateSpec:
    """One gate's complete authorization sentence, as the shared check receives it."""

    module: str
    sensitivity: str
    permission: str
    surface: str
    detail: str
    request: bool = False
    whole_institution: bool = True
    denial_status: int = 403


CREDIT_DETAIL = "Credit access requires an active scoped binding."
MARKETS_VIEW_DETAIL = "Markets access requires an active scoped binding."

#: The frozen policy snapshot. A change here is an access change, not a refactor.
FROZEN_SPECS: dict[str, GateSpec] = {
    "require_capital_aggregated_view": GateSpec(
        module="CAPITAL",
        sensitivity="AGGREGATED",
        permission="VIEW",
        surface="capital_aggregated_view",
        detail="Capital access requires an active scoped binding.",
    ),
    "require_capital_confidential_view": GateSpec(
        module="CAPITAL",
        sensitivity="CONFIDENTIAL",
        permission="VIEW",
        surface="capital_confidential_view",
        detail="Capital access requires an active scoped binding.",
        request=True,
    ),
    "require_capital_restricted_view": GateSpec(
        module="CAPITAL",
        sensitivity="RESTRICTED",
        permission="VIEW",
        surface="capital_restricted_view",
        detail="Capital assurance access requires an active scoped binding.",
    ),
    "require_capital_run": GateSpec(
        module="CAPITAL",
        sensitivity="CONFIDENTIAL",
        permission="RUN",
        surface="capital_run",
        detail="Running Capital calculations requires an active scoped binding.",
    ),
    "require_credit_aggregated_view": GateSpec(
        module="CREDIT",
        sensitivity="AGGREGATED",
        permission="VIEW",
        surface="credit_aggregated_view",
        detail=CREDIT_DETAIL,
    ),
    "require_credit_concentration_view": GateSpec(
        module="CREDIT",
        sensitivity="RESTRICTED",
        permission="VIEW",
        surface="credit_concentration_view",
        detail=CREDIT_DETAIL,
    ),
    "require_credit_restricted_view": GateSpec(
        module="CREDIT",
        sensitivity="RESTRICTED",
        permission="VIEW",
        surface="credit_restricted_view",
        detail=CREDIT_DETAIL,
        request=True,
    ),
    "require_credit_blotter_view": GateSpec(
        module="CREDIT",
        sensitivity="RESTRICTED",
        permission="VIEW",
        surface="credit_blotter_view",
        detail=CREDIT_DETAIL,
        whole_institution=False,
    ),
    "require_credit_activity_view": GateSpec(
        module="CREDIT",
        sensitivity="CONFIDENTIAL",
        permission="VIEW",
        surface="credit_activity_view",
        detail=CREDIT_DETAIL,
        whole_institution=False,
    ),
    "require_credit_run": GateSpec(
        module="CREDIT",
        sensitivity="CONFIDENTIAL",
        permission="RUN",
        surface="credit_run",
        detail="Running Credit calculations requires an active scoped binding.",
    ),
    "require_fx_aggregated_view": GateSpec(
        module="FX",
        sensitivity="AGGREGATED",
        permission="VIEW",
        surface="fx_aggregated_view",
        detail="FX access requires an active scoped binding.",
    ),
    "require_fx_run": GateSpec(
        module="FX",
        sensitivity="CONFIDENTIAL",
        permission="RUN",
        surface="fx_run",
        detail="Running FX calculations requires an active scoped binding.",
    ),
    "require_forecasting_aggregated_view": GateSpec(
        module="FORECASTING",
        sensitivity="AGGREGATED",
        permission="VIEW",
        surface="forecasting_aggregated_view",
        detail="Forecasting access requires an active scoped binding.",
    ),
    "require_forecasting_confidential_view": GateSpec(
        module="FORECASTING",
        sensitivity="CONFIDENTIAL",
        permission="VIEW",
        surface="forecasting_confidential_view",
        detail="Forecasting access requires an active scoped binding.",
    ),
    "require_forecasting_run_detail_view": GateSpec(
        module="FORECASTING",
        sensitivity="CONFIDENTIAL",
        permission="VIEW",
        surface="forecasting_run_detail",
        detail="Regulatory run not found.",
        denial_status=404,
    ),
    "require_forecasting_run": GateSpec(
        module="FORECASTING",
        sensitivity="CONFIDENTIAL",
        permission="RUN",
        surface="forecasting_run",
        detail="Running Forecasting calculations requires an active scoped binding.",
    ),
    "require_forecasting_assumption_edit": GateSpec(
        module="FORECASTING",
        sensitivity="CONFIDENTIAL",
        permission="EDIT",
        surface="forecast_assumption_edit",
        detail="Editing forecast assumptions requires an active scoped binding.",
    ),
    "require_forecasting_assumption_review": GateSpec(
        module="FORECASTING",
        sensitivity="CONFIDENTIAL",
        permission="REVIEW",
        surface="forecast_assumption_review",
        detail="Reviewing forecast assumptions requires an active scoped binding.",
    ),
    "require_markets_published_view": GateSpec(
        module="MARKETS",
        sensitivity="PUBLISHED",
        permission="VIEW",
        surface="markets_published_view",
        detail=MARKETS_VIEW_DETAIL,
    ),
    "require_markets_template_view": GateSpec(
        module="MARKETS",
        sensitivity="PUBLISHED",
        permission="VIEW",
        surface="markets_template_view",
        detail=MARKETS_VIEW_DETAIL,
    ),
    "require_markets_confidential_view": GateSpec(
        module="MARKETS",
        sensitivity="CONFIDENTIAL",
        permission="VIEW",
        surface="markets_confidential_view",
        detail=MARKETS_VIEW_DETAIL,
    ),
    "require_markets_restricted_view": GateSpec(
        module="MARKETS",
        sensitivity="RESTRICTED",
        permission="VIEW",
        surface="markets_restricted_view",
        detail="Market data connection access requires an active scoped binding.",
    ),
    "require_markets_run": GateSpec(
        module="MARKETS",
        sensitivity="CONFIDENTIAL",
        permission="RUN",
        surface="markets_run",
        detail="Running an implied rating requires an active scoped binding.",
    ),
    "require_markets_upload": GateSpec(
        module="MARKETS",
        sensitivity="PUBLISHED",
        permission="CREATE",
        surface="markets_upload",
        detail="Uploading market data requires an active scoped binding.",
    ),
    "require_markets_overlay_create": GateSpec(
        module="MARKETS",
        sensitivity="CONFIDENTIAL",
        permission="CREATE",
        surface="markets_overlay_create",
        detail="Creating a market data overlay requires an active scoped binding.",
    ),
    "require_markets_overlay_edit": GateSpec(
        module="MARKETS",
        sensitivity="CONFIDENTIAL",
        permission="EDIT",
        surface="markets_overlay_edit",
        detail="Ending a market data overlay requires an active scoped binding.",
    ),
}

#: The frozen public shape: signature (annotations included) per gate.
SIMPLE_SIGNATURE = (
    "(db: 'DbSession', ctx: 'Tenant', bank: 'TenantBank') -> 'InstitutionPermissionAccess'"
)
REQUEST_SIGNATURE = (
    "(request: 'Request', db: 'DbSession', ctx: 'Tenant', bank: 'TenantBank') -> 'Instit"
    "utionPermissionAccess'"
)
TEMPLATE_SIGNATURE = (
    "(bank_id: \"Annotated[str, Query(description='Institution the template will be uplo"
    "aded for (exact BK-* id).')]\", db: 'DbSession', ctx: 'Tenant', bank: 'TenantB"
    "ank') -> 'InstitutionPermissionAccess'"
)

FROZEN_SIGNATURES: dict[str, str] = {
    "require_capital_aggregated_view": SIMPLE_SIGNATURE,
    "require_capital_confidential_view": REQUEST_SIGNATURE,
    "require_capital_restricted_view": SIMPLE_SIGNATURE,
    "require_capital_run": SIMPLE_SIGNATURE,
    "require_credit_aggregated_view": SIMPLE_SIGNATURE,
    "require_credit_concentration_view": SIMPLE_SIGNATURE,
    "require_credit_restricted_view": REQUEST_SIGNATURE,
    "require_credit_blotter_view": SIMPLE_SIGNATURE,
    "require_credit_activity_view": SIMPLE_SIGNATURE,
    "require_credit_run": SIMPLE_SIGNATURE,
    "require_fx_aggregated_view": SIMPLE_SIGNATURE,
    "require_fx_run": SIMPLE_SIGNATURE,
    "require_forecasting_aggregated_view": SIMPLE_SIGNATURE,
    "require_forecasting_confidential_view": SIMPLE_SIGNATURE,
    "require_forecasting_run_detail_view": SIMPLE_SIGNATURE,
    "require_forecasting_run": SIMPLE_SIGNATURE,
    "require_forecasting_assumption_edit": SIMPLE_SIGNATURE,
    "require_forecasting_assumption_review": SIMPLE_SIGNATURE,
    "require_markets_published_view": SIMPLE_SIGNATURE,
    "require_markets_template_view": TEMPLATE_SIGNATURE,
    "require_markets_confidential_view": SIMPLE_SIGNATURE,
    "require_markets_restricted_view": SIMPLE_SIGNATURE,
    "require_markets_run": SIMPLE_SIGNATURE,
    "require_markets_upload": SIMPLE_SIGNATURE,
    "require_markets_overlay_create": SIMPLE_SIGNATURE,
    "require_markets_overlay_edit": SIMPLE_SIGNATURE,
}

#: The frozen docstrings (None for the gates that have none).
FROZEN_DOCS: dict[str, str | None] = {
    "require_capital_aggregated_view": None,
    "require_capital_confidential_view": None,
    "require_capital_restricted_view": None,
    "require_capital_run": None,
    "require_credit_aggregated_view": (
        "CREDIT / ``aggregated`` / ``view``, whole institu"
        "tion.\n"
        "\n"
        "The dashboard, the migration matrix, the vintage "
        "curves and the advisory PDs\n"
        "are the institution's portfolio figures: the dash"
        "board's NPL ratio is\n"
        "measured against the resolved prudential ceiling,"
        " and the other three are\n"
        "rates over the institution's whole book. None of "
        "them says in its payload\n"
        "what population it was computed over, so a branch"
        "-scoped slice would read as\n"
        "the institution's number — refused instead.\n"
    ),
    "require_credit_concentration_view": (
        "CREDIT / ``restricted`` / ``view``, whole inst"
        "itution.\n"
        "\n"
        "The concentration monitor's ``single_name`` di"
        "mension buckets on\n"
        "``cp:<counterparty name>`` / ``group:<referenc"
        "e>`` and its breach list\n"
        "carries those keys, so the payload names oblig"
        "ors — ``restricted``, the same\n"
        "sensitivity as the blotter, not the ``aggregat"
        "ed`` its route line once\n"
        "assumed. It also compares each bucket against "
        "a Board limit expressed as a\n"
        "share of the institution's capital, which is a"
        "n institution-grain measure a\n"
        "branch slice cannot produce.\n"
    ),
    "require_credit_restricted_view": (
        "CREDIT / ``restricted`` / ``view``, whole institu"
        "tion.\n"
        "\n"
        "Named obligor and Board-policy surfaces take this"
        " one: the concentration\n"
        "limit register stores ``cp:<counterparty>`` / ``g"
        "roup:<reference>`` buckets,\n"
        "the SDI large-exposure read lists counterparty na"
        "mes, and the four credit\n"
        "registers are the Board's adopted assumptions and"
        " limits. They are\n"
        "``restricted`` because the issue's own classifica"
        "tion says so — loan books,\n"
        "borrower/position details, assumptions and run in"
        "puts are restricted — and\n"
        "because a register row can name a counterparty.\n"
        "\n"
        "Whole institution, because these are the institut"
        "ion's adopted policy and\n"
        "the large-exposure percentages are measured again"
        "st Net Own Funds: a branch\n"
        "grant cannot produce a smaller register, and a sl"
        "iced exposure list would be\n"
        "read against an institution limit. A surface that"
        " genuinely owns branch\n"
        "rows must opt out of that default and apply the s"
        "cope; see\n"
        "``require_credit_blotter_view`` and ``require_cre"
        "dit_activity_view``.\n"
    ),
    "require_credit_blotter_view": (
        "CREDIT / ``restricted`` / ``view``, data scope APPLI"
        "ED.\n"
        "\n"
        "The blotter and its facet counts are named obligor-l"
        "evel rows, each carrying\n"
        "its own branch, so they are the surface a branch or "
        "region grant was written\n"
        "for: the caller applies ``access.data_scope`` to the"
        " rows AND to every count.\n"
    ),
    "require_credit_activity_view": (
        "CREDIT / ``confidential`` / ``view``, data scope AP"
        "PLIED.\n"
        "\n"
        "Loan activity is a record-level grid — one row per "
        "restructure, write-off and\n"
        "recovery, with the facility reference and the amoun"
        "t — and it carries no\n"
        "counterparty name. ``confidential`` is what the pla"
        "tform's own sensitivity\n"
        "rule says a record-level grid without an obligor id"
        "entity is; it is NOT the\n"
        "blotter's ``restricted``, so neither sentence impli"
        "es the other. Each event\n"
        "is attributable to a branch through the facility it"
        " names, so the scope\n"
        "applies to the rows, the two counts and the monthly"
        " flows.\n"
    ),
    "require_credit_run": (
        "CREDIT / ``confidential`` / ``run``, whole institution.\n"
        "\n"
        "Running the credit scenario batch MINTS immutable ``Regulator"
        "yRun`` rows —\n"
        "the provenance a filed credit figure cites — so it is ``run``"
        ", never ``view``,\n"
        "and the whole institution, because a run sealed over one bran"
        "ch would be a\n"
        "filing record that claims to be the institution's book.\n"
    ),
    "require_fx_aggregated_view": None,
    "require_fx_run": None,
    "require_forecasting_aggregated_view": None,
    "require_forecasting_confidential_view": None,
    "require_forecasting_run_detail_view": (
        "Confidential view for one run addressed by i"
        "ts opaque ID.\n"
        "\n"
        "A run the caller may not open is indistingui"
        "shable from one that does not\n"
        "exist, so the denial is the same 404 the ser"
        "vice raises for an unknown ID.\n"
    ),
    "require_forecasting_run": None,
    "require_forecasting_assumption_edit": (
        "Draft, revise or submit a forecast assumption version (the maker's verbs)."
    ),
    "require_forecasting_assumption_review": (
        "Reach the checker's decision on a forecast"
        " assumption version.\n"
        "\n"
        "``review`` is the route's contract; the se"
        "rvice then requires ``approve``\n"
        "with the maker-checker verdict for the exa"
        "ct version, which only it can\n"
        "load.\n"
    ),
    "require_markets_published_view": None,
    "require_markets_template_view": (
        "Bind the organization-level template download to o"
        "ne named institution.\n"
        "\n"
        "The workbook itself carries no tenant data, but th"
        "e effective-authority\n"
        "projection has no organization-wide Markets capabi"
        "lity, so an\n"
        "organization-target check would leave every instit"
        "ution-scoped analyst\n"
        "unable to fetch the template they are allowed to u"
        "pload. Naming the bank\n"
        "lets the same exact grant the Markets hub needs de"
        "cide the download.\n"
    ),
    "require_markets_confidential_view": None,
    "require_markets_restricted_view": None,
    "require_markets_run": None,
    "require_markets_upload": None,
    "require_markets_overlay_create": None,
    "require_markets_overlay_edit": None,
}


def _capture_handed_policy(monkeypatch: pytest.MonkeyPatch) -> dict[str, GateSpec]:
    """Call each gate with inert dummies and record the sentence it passes on."""
    captured: dict[str, GateSpec] = {}
    handed: dict[str, dict[str, Any]] = {}
    examiner_request = object()

    def _record(db: object, ctx: object, bank: object, **kwargs: Any) -> object:
        handed["current"] = kwargs
        return _SENTINEL

    monkeypatch.setattr(deps, "_require_institution_permission", _record)
    for name in GATE_NAMES:
        gate = getattr(deps, name)
        arguments: list[object] = []
        for parameter in inspect.signature(gate).parameters:
            if parameter == "request":
                arguments.append(examiner_request)
            elif parameter == "bank_id":
                arguments.append("BK-IMPSNAP0001")
            else:
                arguments.append(object())
        assert gate(*arguments) is _SENTINEL
        kwargs = handed["current"]
        captured[name] = GateSpec(
            module=kwargs["module"].name,
            sensitivity=kwargs["sensitivity"].name,
            permission=kwargs["permission"].name,
            surface=kwargs["surface"],
            detail=kwargs["detail"],
            request=kwargs.get("request") is not None,
            whole_institution=kwargs.get("require_whole_institution", True),
            denial_status=kwargs.get("denial_status", 403),
        )
    return captured


_SENTINEL = object()


def test_every_simple_gate_hands_the_frozen_policy_to_the_shared_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _capture_handed_policy(monkeypatch) == FROZEN_SPECS


def test_the_table_covers_exactly_the_simple_gates() -> None:
    assert set(FROZEN_SPECS) == set(GATE_NAMES)
    assert len(FROZEN_SPECS) == 26


def test_the_simple_gates_keep_their_public_shape() -> None:
    for name in GATE_NAMES:
        gate = getattr(deps, name)
        assert gate.__name__ == name, name
        assert gate.__qualname__ == name, name
        assert gate.__module__ == "app.api.deps", name
        assert str(inspect.signature(gate)) == FROZEN_SIGNATURES[name], name
        assert gate.__doc__ == FROZEN_DOCS[name], name


def test_the_frozen_table_rows_equal_the_policy_snapshot() -> None:
    """The spec table is the snapshot, spelled as data."""
    table: Mapping[str, Any] = deps._INSTITUTION_GATE_SPECS
    assert {
        name: GateSpec(
            module=row.module.name,
            sensitivity=row.sensitivity.name,
            permission=row.permission.name,
            surface=row.surface,
            detail=row.detail,
            request=row.request,
            whole_institution=row.whole_institution,
            denial_status=row.denial_status,
        )
        for name, row in table.items()
    } == FROZEN_SPECS


def test_each_simple_gate_defers_to_its_frozen_spec_row() -> None:
    """A gate may not grow a second, hand-written policy beside the table."""
    for name in GATE_NAMES:
        source = inspect.getsource(getattr(deps, name))
        assert f'_INSTITUTION_GATE_SPECS["{name}"]' in source, name
