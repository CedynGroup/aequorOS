"""Country identity stays DATA in the ICAAP build, never code.

The regulator's name, the currency and the country belong to the jurisdictions
registry and to the framework JSON under ``frameworks/<jurisdiction>/``. A
Nigerian bank must read its own supervisor's name in the same sentence a
Ghanaian bank reads its own, which is only true if no ICAAP module names one.

Only string literals and identifiers are scanned — what a bank could ever see.
Comments and docstrings explain the rules, and cite the regulator's paragraphs
by name to do so; explanation is not output. The earlier neutrality guard
matched line prefixes and had a trailing-comment false positive, which reading
the AST cannot repeat.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.domain.icaap.frameworks import registry
from tests.fixtures.reference_data import GHANA

BACKEND = Path(__file__).parents[2]

#: Everything the ICAAP work owns, except the framework data itself.
SCANNED_ROOTS = ("app/domain/icaap", "app/services/icaap")
SCANNED_FILES = (
    "app/schemas/icaap.py",
    "app/features/manage_icaap.py",
    "app/features/manage_icaap_sections.py",
    "app/features/manage_icaap_blocks.py",
    "app/features/manage_icaap_attachments.py",
    "app/models/icaap.py",
)

#: P5 modules the ICAAP consumes but does not own (P5-DESIGN §5.5). The IRRBB
#: standardised framework keys its shocks by CURRENCY, the granularity
#: adjustment is jurisdiction-neutral arithmetic, and the run service resolves
#: both through the governed parameter plane — so a country name appearing in
#: any of them would be a calibration nobody could move to another market.
#: Matched as globs so a module that has not been written yet is simply not
#: scanned, rather than failing the whole guard on a missing path.
SCANNED_GLOBS = (
    "app/domain/irr/standardised*.py",
    "app/domain/credit/granularity.py",
    "app/services/regulatory_irr_sf.py",
)

#: Country, regulator and currency identity. Each is resolved from the
#: jurisdictions registry at runtime, never written down.
IDENTITY = re.compile(
    r"\b("
    r"GH|GHS|GHC|NG|NGN|KE|KES|ZA|ZAR"
    r"|BoG|BOG|CBN|CBK|SARB"
    r"|Bank of Ghana|Central Bank of Nigeria|Central Bank of Kenya"
    r"|Ghana|Ghanaian|Nigeria|Nigerian|Kenya|Kenyan"
    r"|cedi|cedis|naira|shilling"
    r"|GoG|Act 930"
    r")\b"
)


#: The seed catalogue names the jurisdiction its rows are for and cites the
#: paragraphs they come from — exactly as ``regulatory_parameters.SEED_PARAMETERS``
#: does. That IS the data; scanning it would be scanning the registry.
DELIBERATE_EXCEPTIONS = frozenset({"app/services/icaap/parameters.py"})


def _files() -> list[Path]:
    found = [
        path
        for root in SCANNED_ROOTS
        for path in sorted((BACKEND / root).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    found.extend(BACKEND / name for name in SCANNED_FILES)
    found.extend(path for pattern in SCANNED_GLOBS for path in sorted(BACKEND.glob(pattern)))
    return [path for path in found if str(path.relative_to(BACKEND)) not in DELIBERATE_EXCEPTIONS]


FILES = _files()
IDS = [str(path.relative_to(BACKEND)) for path in FILES]


def _code_only(source: str) -> str:
    """Just the string literals and identifiers: what a bank could ever see.

    Comments never reach the AST at all, and docstrings are excluded by
    identity, so the prose that EXPLAINS these rules — which necessarily names
    the regulator — is not mistaken for output. Matching on line prefixes is
    what gave the earlier guard its trailing-comment false positive.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    identifiers = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)] + [
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    ]
    return "\n".join([*literals, *identifiers])


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_no_icaap_module_names_a_country_a_regulator_or_a_currency(path: Path) -> None:
    hits = sorted(set(IDENTITY.findall(_code_only(path.read_text(encoding="utf-8")))))
    assert not hits, (
        f"{path.relative_to(BACKEND)} names {hits}. Country identity is data: resolve "
        "it through app/services/jurisdictions.py, or put it in the framework JSON "
        "under frameworks/<jurisdiction>/."
    )


def test_the_framework_data_is_where_country_identity_belongs() -> None:
    """The exception that proves the rule: the JSON says which country it is."""
    published = registry.load_all()
    assert published
    for framework in published:
        assert framework.jurisdiction
        assert framework.regulator
        source = registry.FRAMEWORK_ROOT / framework.jurisdiction.lower()
        assert source.is_dir(), "a framework lives under its own jurisdiction directory"


def test_the_framework_regulator_matches_the_jurisdictions_registry() -> None:
    """One name for the supervisor, from one place."""

    known = {GHANA["code"]: GHANA["regulator_short"]}
    for framework in registry.load_all():
        expected = known.get(framework.jurisdiction)
        if expected is None:
            continue
        assert framework.regulator.casefold() == expected.casefold()


def test_the_guard_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    violation = tmp_path / "leaky.py"
    violation.write_text(
        '"""A docstring may name Bank of Ghana."""\n\n\n'
        "def label() -> str:\n"
        '    return "Bank of Ghana requires 13% of RWA"  # a comment may too\n',
        encoding="utf-8",
    )
    hits = IDENTITY.findall(_code_only(violation.read_text(encoding="utf-8")))
    assert "Bank of Ghana" in hits
