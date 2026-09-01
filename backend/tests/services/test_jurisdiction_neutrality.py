"""Country/currency identity must stay DATA, not code.

These are guard tests, not behaviour tests. The leaks they cover were all
oversights rather than decisions — a prettier line-wrap that added an explicit
``'GHS'`` argument, an f-string that spelled the unit out, a column default that
quietly made every new bank Ghanaian. Each one is individually harmless-looking
and collectively they make the platform single-country, so the cheapest defence
is a test that fails the moment one comes back.

Deliberate Ghana-factual exceptions (BoG return templates, the sample-bank
fixture, the jurisdictions seed) are listed in CLAUDE.md and excluded here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import Bank, BankFinancialFact, ParamCapitalThreshold, RegulatoryParameter
from app.services.jurisdictions import base_currency

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Modules that compute or narrate bank-facing figures. None of them may name a
# currency: they resolve it from the bank via jurisdictions.base_currency.
_CURRENCY_NEUTRAL_MODULES = (
    "app/services/regulatory_capital.py",
    "app/services/regulatory_liquidity.py",
    "app/services/regulatory_fx.py",
    "app/services/regulatory_irr.py",
    # Credit module (credit PR-2): the loan book's prudential narrative must
    # resolve regulator/currency through the jurisdiction services.
    "app/services/regulatory_credit.py",
    "app/services/loan_classification.py",
    "app/services/fact_derivation.py",
    # Phase 2 calculation/workflow modules join the guard (2026-08-08):
    # narrative in these must resolve currency/regulator through the
    # jurisdiction services, never a literal.
    "app/services/liquidity_ewi.py",
    "app/services/liquidity_cfp.py",
    "app/services/liquidity_thresholds.py",
    "app/services/credit_params.py",
    "app/services/capital_plan.py",
    "app/services/reverse_stress.py",
    "app/services/examiner_mode.py",
    "app/domain/capital/ecl.py",
    # Joined the guard 2026-08-21 (enterprise audit §6): enterprise_stress was
    # explicitly called out as a module the guard did not scan, and the policy
    # layer must be neutral by construction.
    "app/services/enterprise_stress.py",
    "app/services/loan_classification.py",
    "app/services/regulatory_forecasting.py",
    "app/services/regulatory_parameters.py",
    "app/services/institution_types.py",
    "app/domain/policy/resolver.py",
)

# Modules that must never SUBSTITUTE a country identity when one is missing.
# This is a different defect from naming a currency in narrative: ``(bank.currency
# or "GHS")`` produces a perfectly neutral-looking string and silently converts a
# Nigerian book to cedis. The whole of app/services and app/models is scanned,
# because the pattern spread by copy-paste rather than by design.
_SUBSTITUTION_SCAN_ROOTS = ("app/services", "app/models", "app/schemas", "app/domain")

# ``or ""`` is NOT a substitution — it degrades to "unknown" and the caller then
# handles it. Only a non-empty country/currency literal is the bug.
_SUBSTITUTION_PATTERN = re.compile(r'\bor\s+["\'](?:GH|GHS|NG|NGN|KE|KES|ZA|ZAR)["\']')

# ISO 4217 codes for the jurisdictions the registry seeds, plus the majors the
# curve taxonomy covers. A bare one of these in narrative is the bug.
_CURRENCY_CODES = ("GHS", "NGN", "KES", "ZAR", "EUR", "GBP")

# `USD` is excluded deliberately: it is a legitimate constant in FX code (the
# quote convention, the USD leg of a pair), not a stand-in for "the bank's
# currency". Excluding it keeps this test about the actual defect.


def _narrative_currency_literals(source: str) -> list[str]:
    """Currency codes appearing in a string literal, ignoring comments.

    Docstrings are ignored too: they explain conventions with a worked example
    ("USD/GHS 12.85 means 12.85 GHS per USD"), which is documentation, not
    output. Only code a bank can see is in scope.
    """
    hits: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        # Only lines that are part of an expression producing text.
        if '"' not in line and "'" not in line:
            continue
        for code in _CURRENCY_CODES:
            if re.search(rf'["\'][^"\']*\b{code}\b[^"\']*["\']', line):
                hits.append(f"{code}: {line}")
    return hits


@pytest.mark.parametrize("module_path", _CURRENCY_NEUTRAL_MODULES)
def test_calculation_modules_name_no_currency(module_path: str) -> None:
    """A Nigerian bank must never read "GHS" in its own LCR narrative.

    The regression this pins: ``regulatory_liquidity`` once wrote
    ``f"{lcr.gross_inflows_total} GHS were capped at ..."`` — correct arithmetic,
    wrong unit for every bank outside Ghana.
    """
    source = (_BACKEND_ROOT / module_path).read_text(encoding="utf-8")
    # Strip docstrings before scanning: they carry worked examples on purpose.
    without_docstrings = re.sub(r'"""(?:.|\n)*?"""', "", source)
    leaks = _narrative_currency_literals(without_docstrings)
    assert not leaks, (
        f"{module_path} names a currency in bank-facing text. Resolve it from "
        f"the bank via jurisdictions.base_currency instead:\n  " + "\n  ".join(leaks)
    )


def test_no_module_substitutes_a_country_identity() -> None:
    """``(bank.jurisdiction_code or "GH")`` must not exist anywhere.

    Every one of these was a copy-paste of the same line, and each one re-creates
    the trap ``banks.currency``/``banks.jurisdiction_code`` were made mandatory to
    close: a bank licensed elsewhere silently inherits Ghana's parameter set —
    CAR floor, provisioning grid, DPD boundaries, LMTD floors — or reports in
    cedis. Resolve through ``jurisdictions.jurisdiction_code`` /
    ``jurisdictions.base_currency``, which raise instead of guessing.
    """
    leaks: list[str] = []
    for root in _SUBSTITUTION_SCAN_ROOTS:
        for path in sorted((_BACKEND_ROOT / root).rglob("*.py")):
            # Docstrings are stripped first: they describe the defect on purpose
            # (this module's own docstring quotes the pattern it forbids).
            source = re.sub(r'"""(?:.|\n)*?"""', "", path.read_text(encoding="utf-8"))
            for number, raw in enumerate(source.splitlines(), 1):
                line = raw.strip()
                if line.startswith("#"):
                    continue
                if _SUBSTITUTION_PATTERN.search(line):
                    leaks.append(f"{path.relative_to(_BACKEND_ROOT)}:~{number}: {line}")
    assert not leaks, (
        "A country/currency identity is being substituted rather than resolved:\n  "
        + "\n  ".join(leaks)
    )


def test_parameter_tables_carry_no_jurisdiction_default() -> None:
    """The jurisdiction is part of a governed parameter's IDENTITY.

    ``RegulatoryParameterMixin`` defaulted ``jurisdiction_code="GH"`` and is
    inherited by NINE parameter tables, so one default filed every board-register
    generation under Ghana — a Nigerian tenant's included (enterprise audit §6).
    ``bank_financial_facts.currency`` defaulted to "GHS" the same way.
    """
    checks = (
        (ParamCapitalThreshold, "jurisdiction_code"),
        (RegulatoryParameter, "jurisdiction_code"),
        (BankFinancialFact, "currency"),
    )
    for model, column_name in checks:
        column = model.__table__.columns[column_name]
        assert column.default is None, (
            f"{model.__tablename__}.{column_name} must not carry a default — it is "
            "part of the row's identity and belongs to the write site."
        )
        assert not column.nullable


def test_bank_model_carries_no_jurisdiction_default() -> None:
    """currency and jurisdiction_code are required, never defaulted.

    Independent defaults ("GHS" and "GH") could silently disagree, so a bank
    created with jurisdiction_code="NG" kept reporting in cedis. Requiring both
    turns that into a loud failure at the creation site.
    """
    for column_name in ("currency", "jurisdiction_code"):
        column = Bank.__table__.columns[column_name]
        assert column.default is None, (
            f"banks.{column_name} must not carry a default — it decides the "
            "institution's country identity and belongs to the creation site."
        )
        assert column.server_default is None
        assert not column.nullable


def test_base_currency_refuses_to_guess() -> None:
    """No fallback. An unset currency is a skipped decision, not a Ghanaian bank."""
    bank = Bank(
        id="BK-TEST0001",
        organization_id="OR-TEST0001",
        name="Unset Currency Bank",
        short_name="UCB",
        currency="",
        jurisdiction_code="NG",
        license_type="universal",
        institution_type="universal_bank",
    )
    with pytest.raises(ValueError, match="no reporting currency"):
        base_currency(bank)


def test_every_seeded_jurisdiction_can_ingest_its_own_market_data() -> None:
    """The DataScope enum gates INGESTION, not just vendor pulls.

    ``manual_upload/parser`` resolves ``YIELD_CURVE_{currency}`` and
    ``FX_SPOT_{base}_{quote}`` against the taxonomy and rejects the row when no
    scope exists ("unsupported currency ... no yield curve scope exists"). So a
    jurisdiction in the registry with no scopes cannot get its data into the
    platform at all — by the Excel/CSV route, the API-push route, or any other.

    That is how Kenya and South Africa came to be seeded jurisdictions that could
    upload a yield curve but not their own USD spot rate. This test is the thing
    that should fail the next time a jurisdiction is added without its scopes.

    Scoped to curve + FX spot deliberately: those are what the calculation layer
    actually consumes (``fact_derivation`` reads exactly three market-data
    functions). Macro and security master are display/aspiration and are left to
    the LSEG trial to size — see docs/lseg_trial_scope.md §2.
    """
    # Mirrors the jurisdictions registry seed (GH/NG/KE/ZA). Kept literal rather
    # than read from the DB so the test is hermetic and fails on the ADDITION of
    # a jurisdiction, which is exactly when the scopes get forgotten.
    seeded = {"GH": "GHS", "NG": "NGN", "KE": "KES", "ZA": "ZAR"}
    scope_names = {scope.value for scope in DataScope}

    missing: list[str] = []
    for code, currency in sorted(seeded.items()):
        for required in (f"YIELD_CURVE_{currency}", f"FX_SPOT_USD_{currency}"):
            if required not in scope_names:
                missing.append(f"{code} ({currency}) -> {required}")

    assert not missing, (
        "Seeded jurisdictions cannot ingest their own market data. Add the scope "
        "to DataScope and a verification_required entry to BOTH vendor catalogs "
        "(never invent a RIC or security — §16.4):\n  " + "\n  ".join(missing)
    )


def test_base_currency_normalises() -> None:
    bank = Bank(
        id="BK-TEST0002",
        organization_id="OR-TEST0001",
        name="Nigeria Test Bank",
        short_name="NTB",
        currency=" ngn ",
        jurisdiction_code="NG",
        license_type="universal",
        institution_type="universal_bank",
    )
    assert base_currency(bank) == "NGN"
