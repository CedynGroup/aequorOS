"""No regulatory or methodological number is written into the ICAAP build.

Founder directive D-024: "Don't hardcode any number but fetch from console."
Every deadline, threshold, band and horizon the ICAAP workspace applies or
prints is a row in the regulatory-parameter control plane that staff edit,
propose and approve — so changing one is an operator action, not a release.

Two allowlists make this checkable rather than merely aspirational:

* the SEED CATALOGUE (``app/services/icaap/parameters.py``) and the migration
  that pins it are where values are permitted, because D-024 §2 says the
  initial approved rows have to live somewhere;
* a short, reviewed list of STRUCTURAL constants — sizes, limits, scales,
  indices — which are not regulatory numbers at all. Each entry names why.

Everything else under ``app/domain/icaap`` and ``app/services/icaap`` must be
free of numeric literals beyond the trivial ones, and the framework JSON must
carry parameter CODES rather than figures.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.domain.icaap.frameworks import registry
from app.services.icaap.parameters import (
    ICAAP_JURISDICTION_SEED_PARAMETERS,
    ICAAP_PARAM_CODES,
    ICAAP_SEED_PARAMETERS,
)
from app.services.regulatory_parameters import (
    FILING_PARAMETER_CODES,
    P2_PARAMETER_CODES,
    P5_PARAMETER_CODES,
)

BACKEND = Path(__file__).parents[2]
SCANNED = ("app/domain/icaap", "app/services/icaap")

#: The one file D-024 §2 permits to hold values, plus the migration that pins
#: them. A test below proves the two agree.
SEED_CATALOGUE = "app/services/icaap/parameters.py"
SEED_MIGRATION = "alembic/versions/202609190055_icaap_workspace.py"

#: Numbers that are not regulatory: 0 and 1 are identity and emptiness, 2 is a
#: pair, and -1 is "the last one".
TRIVIAL = frozenset({0, 1, 2, -1})

#: Page geometry and typography. Font sizes, margins and column widths are not
#: regulatory values, and a renderer only ever prints figures it was handed —
#: so these files are exempt from the NUMERIC scan but stay in the percentage
#: scan below, which is what would catch a regulatory claim in their copy.
LAYOUT_ONLY = ("app/services/icaap/render/", "app/services/icaap/exports_draft.py")

#: Structural constants, each with the reason it is not a regulatory value.
#: Adding a line here is a review decision, not a workaround.
STRUCTURAL_ALLOWLIST: dict[str, dict[int | float, str]] = {
    "app/domain/icaap/prosemirror.py": {
        0xD800: "Unicode surrogate range, lower bound",
        0xDFFF: "Unicode surrogate range, upper bound",
        3: "heading level 3, a document structure level",
        4: "heading level 4, a document structure level",
    },
    "app/domain/icaap/frameworks/schema.py": {
        280: "maximum characters in a paraphrased checklist item (editorial)",
        12: "months in a year, for month arithmetic",
        26: "letters in the alphabet, for section lettering beyond z",
    },
    "app/services/icaap/blocks.py": {
        40: "maximum characters in a generated block key slug",
    },
    "app/services/icaap/readiness_p2.py": {
        12: "months in a year, for the age of a review or a Board approval",
    },
    "app/services/icaap/sections.py": {
        10: "minimum characters in a waiver reason (editorial)",
        3: "heading level 3, used for the provenance heading on merged text",
    },
    "app/services/icaap/resolvers/plans.py": {
        5: "number of projection years the plan block republishes",
    },
    "app/services/icaap/resolvers/credit.py": {
        10000: "the concentration monitor's index scale, converted to 0-1 here",
    },
    "app/services/icaap/attachments.py": {
        64: "length of a hex sha256 digest",
        255: "maximum stored filename length, matching the column",
        200: "maximum stored source-reference length for object metadata",
    },
    # --- P3 -----------------------------------------------------------------
    # None of these is a regulatory value: a supervisor does not say how many
    # stages a review chain may have, how long a job title may be, or how many
    # item ids a refusal message lists. Each matches a column width, a database
    # CHECK, or an editorial limit on a sentence.
    "app/domain/icaap/workflow.py": {
        20: "maximum stages in a review chain, matching the CHECK on stage seq",
        10: "maximum officer titles per stage (editorial)",
        120: "maximum characters in an officer title, matching the column",
        200: "maximum characters in a stage title, matching the column",
    },
    "app/services/icaap/workflow.py": {
        10: "minimum characters in a send-back comment (editorial), as for a waiver reason",
    },
    "app/services/icaap/freeze.py": {
        5: "how many section keys a refusal names before it stops being readable",
        2000: "maximum package notes length, matching the column",
    },
    "app/services/icaap/clone.py": {
        200: "maximum cycle title length, matching the column",
    },
    "app/services/icaap/validation_rules.py": {
        20: "how many item ids a validation finding names before it stops being readable",
    },
    # --- P5 -----------------------------------------------------------------
    "app/services/icaap/pillar2_inputs/granularity.py": {
        5: "how many exposure references a book refusal names before it summarises",
    },
}

#: Percentages in ICAAP-facing copy. A percentage literal in a sentence a bank
#: reads is a regulatory claim.
PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?%")
PARAM_PLACEHOLDER = re.compile(r"\{param:[a-z][a-z0-9_]{2,60}\}")
#: Digits in framework data, outside the structural fields that must carry them.
STRUCTURAL_JSON_KEYS = frozenset(
    {"order", "number", "seq", "min_count", "max_count", "score", "issued"}
)


def _python_files(relative: str) -> list[Path]:
    root = BACKEND / relative
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


ALL_FILES = [path for relative in SCANNED for path in _python_files(relative)]
SCANNED_FILES = [
    path
    for path in ALL_FILES
    if str(path.relative_to(BACKEND)) != SEED_CATALOGUE
    and not str(path.relative_to(BACKEND)).startswith(LAYOUT_ONLY)
]
FILE_IDS = [str(path.relative_to(BACKEND)) for path in SCANNED_FILES]
COPY_FILES = [path for path in ALL_FILES if str(path.relative_to(BACKEND)) != SEED_CATALOGUE]
COPY_IDS = [str(path.relative_to(BACKEND)) for path in COPY_FILES]


def _numeric_literals(tree: ast.AST) -> list[tuple[int, int | float]]:
    found: list[tuple[int, int | float]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        found.append((node.lineno, value))
    return found


@pytest.mark.parametrize("path", SCANNED_FILES, ids=FILE_IDS)
def test_no_icaap_module_states_a_regulatory_number(path: Path) -> None:
    relative = str(path.relative_to(BACKEND))
    allowed = STRUCTURAL_ALLOWLIST.get(relative, {})
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offending = [
        (line, value)
        for line, value in _numeric_literals(tree)
        if value not in TRIVIAL and value not in allowed
    ]
    assert not offending, (
        f"{relative} states the number(s) {offending}. A regulatory or methodological "
        "value is a governed parameter resolved through the control plane (D-024); a "
        "structural constant belongs in STRUCTURAL_ALLOWLIST with its reason."
    )


@pytest.mark.parametrize("path", COPY_FILES, ids=COPY_IDS)
def test_no_icaap_module_prints_a_percentage(path: Path) -> None:
    """A percentage in a sentence a bank reads is a regulatory claim."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip() == "%":
                continue  # the unit label on a ratio fact
            if PERCENT.search(node.value):
                offending.append(node.value)
    assert not offending, f"{path.relative_to(BACKEND)} prints {offending}"


def test_the_framework_data_carries_parameter_codes_not_figures() -> None:
    """D-024 §3: the JSON references codes; the values live in the console."""
    for framework in registry.load_all():
        assert framework.deadline.months_after_fye_param
        assert framework.materiality.rating_bands_param
        assert framework.materiality.material_min_score_param
        assert framework.materiality.material_min_impact_param
        if framework.disclosure is not None:
            assert framework.disclosure.submit_months_after_fye_param is not None
        for item in framework.all_items():
            assert not PERCENT.search(item.text), item.id
            for code in PARAM_PLACEHOLDER.findall(item.text):
                assert code
            placeholders = set(
                match.group(1)
                for match in re.finditer(r"\{param:([a-z][a-z0-9_]{2,60})\}", item.text)
            )
            assert placeholders <= set(item.param_refs), item.id


def test_no_bare_figure_survives_in_the_framework_json() -> None:
    """A number in the data would be a regulatory value nobody can edit."""

    def walk(node: object, path: str) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in STRUCTURAL_JSON_KEYS:
                    continue
                found.extend(walk(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(walk(value, f"{path}[{index}]"))
        elif isinstance(node, bool):
            return []
        elif isinstance(node, int | float):
            found.append(f"{path} = {node}")
        return found

    for directory in registry.FRAMEWORK_ROOT.glob("*"):
        for source in directory.glob("*.json"):
            raw = json.loads(source.read_text(encoding="utf-8"))
            offending = walk(raw, source.name)
            assert not offending, offending


def test_every_parameter_the_framework_quotes_is_seeded() -> None:
    """A checklist item quoting a code nobody seeded would refuse at read time.

    There are FOUR ICAAP seed catalogues, and a framework may quote from any of
    them. P1's (``services/icaap/parameters.py``, pinned by ``202609190055``)
    holds the workspace codes; P2's (``ICAAP_P2_SEED_PARAMETERS``, pinned by
    ``202609190056``) holds the Pillar 2 engine's, including the review and
    independent-review intervals the Nigeria and Kenya checklists quote; P3's
    (``ICAAP_FILING_SEED_PARAMETERS``, pinned by ``202609190059``) holds the two
    the filing plane introduced; P5's (``P5_SEED_PARAMETERS``) holds the date
    from which the IRRBB standardised framework is mandated, which the Ghana
    framework's method mandate names. Each has its own test proving the
    migration and the catalogue agree, so checking against the union is the same
    guarantee over the complete set rather than a relaxation of it — and
    checking against a subset would report a code that IS seeded as missing.

    A fifth catalogue (``ICAAP_JURISDICTION_SEED_PARAMETERS``, pinned by
    ``202609200063``) seeds Nigeria and Kenya. It introduces no new CODE — it
    repeats these under a different jurisdiction — so this union is complete
    without it, and a test in
    ``tests/services/test_regulatory_parameters_icaap_jurisdictions.py`` fails
    if that ever stops being true. Whether a code is seeded for the right
    JURISDICTION is a different question, asked by
    ``tests/domain/icaap/test_framework_param_refs.py``.
    """
    seeded = ICAAP_PARAM_CODES | P2_PARAMETER_CODES | FILING_PARAMETER_CODES | P5_PARAMETER_CODES
    for framework in registry.load_all():
        missing = sorted(framework.param_refs() - seeded)
        assert not missing, f"{framework.code} references unseeded parameter(s) {missing}"


def test_the_migration_pins_exactly_what_the_catalogue_holds() -> None:
    """A later catalogue edit must not change what the revision seeded."""
    from importlib import util  # noqa: PLC0415 - loading a migration by path

    spec = util.spec_from_file_location("_icaap_seed_migration", BACKEND / SEED_MIGRATION)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pinned = {
        code: (value, value_json, unit, status)
        for code, value, value_json, unit, status, _citation in module.SEEDS
    }
    catalogue = {
        seed.param_code: (seed.value, seed.value_json, seed.unit, seed.confirmation_status)
        for seed in ICAAP_SEED_PARAMETERS
    }
    assert pinned == catalogue


def test_the_representative_values_say_they_are_provisional() -> None:
    """D-039: a calibration nobody has confirmed must not read as settled.

    Over BOTH catalogues in the seed file. The Nigerian and Kenyan rows are all
    pending, for a reason the Ghanaian ones do not have — nobody here has read
    the CBN or CBK text (D-076) — and the rule that they must say so is the
    same rule.
    """
    for seed in (*ICAAP_SEED_PARAMETERS, *ICAAP_JURISDICTION_SEED_PARAMETERS):
        assert seed.source_citation
        if seed.confirmation_status == "pending":
            assert any(
                word in seed.source_citation.casefold()
                for word in ("pending", "representative", "platform policy")
            ), (seed.jurisdiction_code, seed.param_code)


def test_the_guard_would_catch_a_deliberate_violation(tmp_path: Path) -> None:
    """Proof the scan reports rather than merely being present."""
    violation = tmp_path / "hardcoded.py"
    violation.write_text('MINIMUM_RATIO = 13\nNOTE = "the minimum is 13%"\n', encoding="utf-8")
    tree = ast.parse(violation.read_text(encoding="utf-8"))
    assert [value for _line, value in _numeric_literals(tree) if value not in TRIVIAL] == [13]
    assert PERCENT.search(violation.read_text(encoding="utf-8"))
