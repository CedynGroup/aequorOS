"""The published ICAAP frameworks load, and their content is pinned.

The digest pin is the point of this file. A cycle records the digest of the
framework it was started under; if a published version were edited in place, a
bank's checklist would change under a report that had already been reviewed.
Changing ``EXPECTED_DIGESTS`` is therefore a deliberate act that says "this is a
new version of the regulator's text", and the correct response to a failure here
is almost always to publish a new version with a ``section_key_map``, not to
update the number.
"""

from __future__ import annotations

import json
from datetime import date
from importlib import util
from pathlib import Path

import pytest

from app.core.config import IcaapSettings
from app.domain.icaap.blocks import BLOCK_TYPES, P1_BLOCK_TYPES
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework
from app.domain.icaap.methods import METHOD_KEYS, NOT_CAPITALISED

GH = ("bog_icaap", "2026.02-ed.1")
NG = ("cbn_srp_icaap", "2021.09")
KE = ("cbk_icaap_gn", "2016.11")

#: Pinned content fingerprints. See the module docstring before changing one.
#: Ghana's has moved twice, both times deliberately and both times platform
#: configuration rather than the regulator's text:
#:
#: 1. P3 added the ``filing`` block (which return code this assessment becomes,
#:    which annex it carries, and the wording each officer signs under).
#: 2. P5 added ``method_mandates`` to the IRRBB component (A6): the date from
#:    which the standardised framework replaces the interim method is governed
#:    data on the framework, not a rule written into the engine. The parameter
#:    code is carried, never the date.
#:
#: The instrument's own text is byte-for-byte unchanged, and the whole ICAAP
#: build is still uncommitted, so no cycle anywhere is pinned to an earlier
#: digest. A change to the regulator's TEXT still means a new version with a
#: ``section_key_map``, not a new number here.
EXPECTED_DIGESTS: dict[tuple[str, str], str] = {
    GH: "49ceca0c2b0e01f2a065211e449936489c34380e0ef0fbc9f84d58a93a84ba08",
    NG: "3a1e800ee733da7a31bcf5d228914ceedc0b3fb266d56ce39366641b558bc92a",
    KE: "73550f2c298fa21b8f532362c906741a6404c6a0a28146649f34da82fc493fe2",
}

#: The regulator's own printed headings, in order. Paraphrasing one of these
#: would misdescribe the report's structure to a supervisor reading it.
GH_SECTION_TITLES = (
    ("a", "executive_summary", "Executive Summary"),
    ("b", "structure_operations", "Structure and Operations"),
    ("c", "governance", "Governance and Management of the ICAAP"),
    ("d", "business_model_strategy", "Business Model and Strategy"),
    ("e", "risk_management_framework", "Risk Management Framework"),
    ("f", "risk_appetite", "Risk Appetite Statement"),
    ("g", "risk_identification_materiality", "Risk Identification and Materiality Assessment"),
    ("h", "pillar2_quantification", "Quantification of Pillar 2 Capital Requirements"),
    ("i", "stress_testing", "Stress Testing"),
    ("j", "capital_planning", "Capital Planning"),
    ("k", "liquidity_planning", "Liquidity Planning and Management"),
    (
        "l",
        "capital_allocation_reconciliation",
        "Capital Allocation and Reconciliation of Internal Capital",
    ),
    ("m", "management_actions", "Management Actions"),
    ("n", "internal_audit_review", "Internal Audit and Review of ICAAP"),
    ("o", "challenge_adoption", "Challenge and Adoption of the ICAAP"),
    ("p", "approval_review_use", "Approval, Review, and Use of ICAAP within the bank"),
    ("q", "internal_models", "Use of Internal Models for Capital Assessment"),
)


@pytest.fixture
def gh() -> Framework:
    return registry.get(*GH)


def test_every_published_framework_loads_and_is_uniquely_identified() -> None:
    frameworks = registry.load_all()
    assert frameworks, "no ICAAP framework is published"
    keys = [(framework.code, framework.version) for framework in frameworks]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(("code", "version"), [GH, NG, KE])
def test_content_digest_is_pinned(code: str, version: str) -> None:
    assert registry.get(code, version).digest == EXPECTED_DIGESTS[(code, version)]


def test_every_published_framework_is_pinned() -> None:
    """A new jurisdiction ships with its fingerprint, or not at all."""
    published = {(framework.code, framework.version) for framework in registry.load_all()}
    assert published == set(EXPECTED_DIGESTS)


def test_digest_ignores_formatting_but_not_content(gh: Framework) -> None:
    """Re-indenting the JSON must not look like a new version of the text."""
    path = next((registry.FRAMEWORK_ROOT / gh.jurisdiction.lower()).glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    reserialised = json.loads(json.dumps(raw, indent=7, sort_keys=True))
    assert registry.framework_digest(reserialised) == gh.digest

    edited = json.loads(json.dumps(raw))
    edited["sections"][0]["title"] = "Executive summary (edited)"
    assert registry.framework_digest(edited) != gh.digest


def test_gh_sections_are_the_regulators_headings_in_order(gh: Framework) -> None:
    assert len(gh.sections) == len(GH_SECTION_TITLES)
    for section, (letter, key, title) in zip(gh.sections, GH_SECTION_TITLES, strict=True):
        assert (section.letter, section.key, section.title) == (letter, key, title)
    assert [section.order for section in gh.sections] == list(range(1, len(gh.sections) + 1))


def test_only_the_sourced_sections_claim_to_be_sourced(gh: Framework) -> None:
    """D-006: the ICAAP Guideline's own paragraphs for (c)-(q) are unread."""
    sourced = {section.key for section in gh.sections if section.source_status == "sourced"}
    assert sourced == {"executive_summary", "structure_operations"}
    pending = [s for s in gh.sections if s.source_status == "pending_primary_text"]
    assert len(pending) == len(gh.sections) - len(sourced)
    for section in pending:
        assert "pending" in section.guidance.casefold()


def test_risk_categories_are_numbered_and_map_to_every_table5_row(gh: Framework) -> None:
    assert [category.number for category in gh.risk_categories] == list(
        range(1, len(gh.risk_categories) + 1)
    )
    declared = {row.key for row in gh.table5_rows}
    mapped = {
        component.table5_row
        for category in gh.risk_categories
        for component in category.components
        if component.table5_row is not None
    }
    assert mapped == declared
    for category in gh.risk_categories:
        for component in category.components:
            assert set(component.allowed_methods) <= METHOD_KEYS
            if component.table5_row is None:
                assert component.allowed_methods == (NOT_CAPITALISED,)


def test_requirement_ids_are_unique_and_every_item_is_cited(gh: Framework) -> None:
    items = gh.all_items()
    assert len({item.id for item in items}) == len(items)
    for item in items:
        assert item.citations
        assert len(item.text) <= 280
        assert '"' not in item.text


def test_evidence_tokens_reference_real_blocks_and_attachments(gh: Framework) -> None:
    kinds = {attachment.kind for attachment in gh.attachments}
    for item in gh.all_items():
        for token in item.evidence:
            if token.startswith("block:"):
                assert token.split(":", 1)[1] in BLOCK_TYPES
            elif token.startswith("attachment:"):
                assert token.split(":", 1)[1] in kinds
    for section in gh.sections:
        assert set(section.data_blocks) <= P1_BLOCK_TYPES


def test_the_board_resolution_is_required_at_submission_and_never_relaxable(
    gh: Framework,
) -> None:
    resolution = gh.attachment("board_resolution")
    assert (resolution.gate, resolution.min_count) == ("submission", 1)
    assert resolution.relaxable_by_signing_policy is False
    for kind in ("senior_management_report", "stress_test_board_minutes"):
        assert gh.attachment(kind).gate == "freeze"


def test_exactly_one_stage_freezes_the_cycle(gh: Framework) -> None:
    freezing = [stage for stage in gh.stages if stage.freeze_on_approve]
    assert len(freezing) == 1
    assert freezing[0].decision == "approve"
    assert [stage.seq for stage in gh.stages] == list(range(1, len(gh.stages) + 1))


def test_no_regulatory_number_is_carried_in_the_framework_data(gh: Framework) -> None:
    """D-024: figures are parameter references, resolved from the console."""
    assert gh.deadline.months_after_fye_param == "icaap_submission_months"
    assert gh.materiality.rating_bands_param
    assert gh.materiality.material_min_score_param
    assert gh.materiality.material_min_impact_param
    assert gh.disclosure is not None
    assert gh.disclosure.submit_months_after_fye_param
    for item in gh.all_items():
        assert set(item.placeholders) <= set(item.param_refs)
    quoted = [item for item in gh.all_items() if item.param_refs]
    assert quoted, "the checklist quotes at least one governed figure"


def test_the_filing_return_is_declared(gh: Framework) -> None:
    assert gh.filing_return_code == "ICAAP-REPORT"


def test_applicable_is_scoped_by_jurisdiction_class_and_date(gh: Framework) -> None:
    enabled = frozenset({gh.code})
    assert registry.applicable(
        "GH", "bank", date(2026, 12, 31), cycle_kind="annual", enabled_codes=enabled
    ) == (gh,)
    # Before the instrument's first reporting date, a real filing cycle cannot
    # pin it — but a rehearsal is a dry run against the new text and may.
    assert (
        registry.applicable(
            "GH", "bank", date(2025, 12, 31), cycle_kind="annual", enabled_codes=enabled
        )
        == ()
    )
    assert registry.applicable(
        "GH", "bank", date(2025, 12, 31), cycle_kind="rehearsal", enabled_codes=enabled
    ) == (gh,)
    assert (
        registry.applicable(
            "GH", "sdi", date(2026, 12, 31), cycle_kind="annual", enabled_codes=enabled
        )
        == ()
    )
    assert (
        registry.applicable(
            "NG", "bank", date(2026, 12, 31), cycle_kind="annual", enabled_codes=enabled
        )
        == ()
    )


def test_a_framework_not_enabled_for_the_deployment_is_not_offered(gh: Framework) -> None:
    assert (
        registry.applicable(
            "GH",
            "bank",
            date(2026, 12, 31),
            cycle_kind="annual",
            enabled_codes=frozenset({"some_other_code"}),
        )
        == ()
    )


def test_an_unpublished_version_is_a_lookup_failure() -> None:
    with pytest.raises(registry.FrameworkNotFound):
        registry.get("bog_icaap", "1999.01")


# ---------------------------------------------------------------------------
# Nigeria and Kenya (P5-E)
#
# Both ship as DATA. Neither adds a line of jurisdiction-aware code: the
# differences a reader might expect to be branches — a four-month filing window
# instead of three, no Table 5, no publication requirement, no return family to
# file into, a risk taxonomy of a different shape — are all carried in the JSON
# and asserted here.
# ---------------------------------------------------------------------------

#: The CBN's printed Annex B §1 headings, in order.
NG_SECTION_TITLES = (
    ("a", "executive_summary", "Executive Summary"),
    ("b", "structure_operations", "Structure and Operations"),
    ("c", "governance_structure", "Governance Structure"),
    ("d", "risk_appetite_framework", "Risk Appetite Framework"),
    ("e", "risk_identification_materiality", "Risk Identification and Materiality Assessment"),
    ("f", "risk_assessment_capital_adequacy", "Risk Assessment and Capital Adequacy"),
    ("g", "stress_testing", "Stress Testing"),
    ("h", "capital_planning", "Capital Planning"),
    (
        "i",
        "capital_allocation_reconciliation",
        "Capital Allocation and Reconciliation of Internal Capital",
    ),
    ("j", "internal_audit_review", "Internal Audit and Review of ICAAP"),
    ("k", "approval_review_use", "Approval, Review, and Use of ICAAP"),
    ("l", "challenges_further_steps", "Challenges and Further Steps"),
    ("m", "icaap_summary", "Summary of Internal Capital Adequacy Assessment Process"),
    ("n", "internal_models", "Use of Internal Models for Capital Assessment"),
)

#: The CBK's printed Annex B §7 headings, in order.
KE_SECTION_TITLES = (
    ("a", "executive_summary", "Executive Summary"),
    ("b", "design_approval_review_use", "Design, Approval, Review, and Use of ICAAP"),
    ("c", "structure_operations", "Structure and Operations"),
    ("d", "governance_structure", "Governance Structure"),
    ("e", "business_plan_strategy", "Summary of business plan and strategy"),
    ("f", "risk_appetite_statement", "Risk Appetite Statement"),
    ("g", "risk_assessment_capital_adequacy", "Risk Assessment and Capital Adequacy"),
    ("h", "methodology_assumptions", "Methodology and Assumptions"),
    ("i", "stress_testing", "Stress Testing"),
    ("j", "capital_planning", "Capital Planning"),
    ("k", "internal_models", "Use of Internal Models for Capital Assessment"),
    ("l", "challenges_further_steps", "Challenges and further steps"),
)

HEADINGS = {GH: GH_SECTION_TITLES, NG: NG_SECTION_TITLES, KE: KE_SECTION_TITLES}


@pytest.fixture
def ng() -> Framework:
    return registry.get(*NG)


@pytest.fixture
def ke() -> Framework:
    return registry.get(*KE)


@pytest.mark.parametrize(("code", "version"), [GH, NG, KE])
def test_sections_are_the_regulators_headings_in_order(code: str, version: str) -> None:
    framework = registry.get(code, version)
    expected = HEADINGS[(code, version)]
    assert len(framework.sections) == len(expected)
    for section, (letter, key, title) in zip(framework.sections, expected, strict=True):
        assert (section.letter, section.key, section.title) == (letter, key, title)


@pytest.mark.parametrize(("code", "version"), [NG, KE])
def test_the_published_instruments_are_sourced_in_full(code: str, version: str) -> None:
    """The CBN and CBK instruments are published finals that were read whole.

    Ghana's is an exposure draft whose paragraphs behind (c)-(q) are still
    unobtained (D-006), so its sections carry ``pending_primary_text`` and a
    real filing cannot freeze on them. These two are the opposite case, and the
    assertion is deliberately strict: if a future extraction turns out to be
    partial, the section is downgraded and this fails rather than a bank filing
    against a checklist nobody read.
    """
    framework = registry.get(code, version)
    assert framework.status == "final"
    assert [section.source_status for section in framework.sections] == [
        "sourced" for _ in framework.sections
    ]


def test_nigeria_and_kenya_state_their_shape_rather_than_branching(
    ng: Framework, ke: Framework
) -> None:
    """A2, A3, A7: what these regulators do NOT ask for is data, not a branch."""
    for framework in (ng, ke):
        # A2 - Table 5 is the BoG Stress Testing Appendix II grid and nothing else.
        assert framework.table5_rows == ()
        assert framework.table5_citation is None
        # A3 - neither instrument requires publication of the ICAAP.
        assert framework.disclosure is None
        # A7 - no CBN or CBK return family exists, so freeze must refuse rather
        # than invent a return code.
        assert framework.filing_return_code is None
        assert framework.filing is None


def test_a_risk_with_no_capital_line_needs_no_table5_row(ng: Framework, ke: Framework) -> None:
    """The A2 relaxation is narrow: only the column goes, not the rule."""
    for framework in (ng, ke):
        components = [
            component for category in framework.risk_categories for component in category.components
        ]
        assert components
        assert all(component.table5_row is None for component in components)


def test_the_bank_defined_category_carries_a_platform_title(ng: Framework, ke: Framework) -> None:
    """A9: both regulators say their list is not exhaustive, and neither prints
    a heading for what a bank adds. The title is the platform's, and says so."""
    for framework in (ng, ke):
        custom = [category for category in framework.risk_categories if category.custom]
        assert len(custom) == 1
        assert custom[0].title_status == "platform"
    for category in registry.get(*GH).risk_categories:
        assert category.title_status != "platform"


def test_only_a_framework_that_mandates_a_method_carries_a_mandate(
    gh: Framework, ng: Framework, ke: Framework
) -> None:
    """A6: the IRRBB standardised framework is mandated by the BoG text alone.

    The mandate is framework data (which method, from which governed date, in
    place of which) so that a Nigerian or Kenyan cycle simply has none, rather
    than the engine asking which country it is in.
    """
    mandates = [
        mandate
        for category in gh.risk_categories
        for component in category.components
        for mandate in component.method_mandates
    ]
    assert len(mandates) == 1
    assert mandates[0].method == "irrbb_standardised_framework"
    assert mandates[0].replaces == "irrbb_interim_delta_eve"
    assert mandates[0].mandatory_from_param == "irrbb_sf_mandatory_from_as_of"
    assert "irrbb_sf_mandatory_from_as_of" in gh.param_refs()

    for framework in (ng, ke):
        assert not [
            mandate
            for category in framework.risk_categories
            for component in category.components
            for mandate in component.method_mandates
        ]
        assert "irrbb_sf_mandatory_from_as_of" not in framework.param_refs()


@pytest.mark.parametrize(
    ("code", "version", "months", "expected"),
    [
        (*GH, 3, date(2027, 3, 31)),
        (*NG, 4, date(2027, 4, 30)),
        (*KE, 4, date(2027, 4, 30)),
    ],
)
def test_the_deadline_is_the_governed_months_after_the_year_end(
    code: str, version: str, months: int, expected: date
) -> None:
    """A1: Nigeria's and Kenya's four-month window is a console row, not a fork.

    The CBK prints a date (30 April for the 31 December position) and the CBN a
    duration (four months); both are the same governed number here, and the
    month-end clamp makes them agree. Changing any of the three is an operator
    action, which is the whole point of D-024.
    """
    framework = registry.get(code, version)
    assert framework.deadline.months_after_fye_param == "icaap_submission_months"
    assert framework.deadline.due_date(date(2026, 12, 31), months) == expected


@pytest.mark.parametrize(
    ("code", "version", "jurisdiction"),
    [(*GH, "GH"), (*NG, "NG"), (*KE, "KE")],
)
def test_each_framework_is_offered_only_in_its_own_jurisdiction(
    code: str, version: str, jurisdiction: str
) -> None:
    framework = registry.get(code, version)
    enabled = frozenset({framework.code})
    as_of = date(2026, 12, 31)
    assert registry.applicable(
        jurisdiction, "bank", as_of, cycle_kind="annual", enabled_codes=enabled
    ) == (framework,)
    for elsewhere in ("GH", "NG", "KE", "ZA"):
        if elsewhere == jurisdiction:
            continue
        assert (
            registry.applicable(
                elsewhere, "bank", as_of, cycle_kind="annual", enabled_codes=enabled
            )
            == ()
        )


def test_the_deployment_flag_is_what_publishes_a_jurisdiction() -> None:
    """``ICAAP_FRAMEWORKS_ENABLED`` defaults to Ghana; NG and KE are opt-in.

    D-046 §4: this is a registry scope for which jurisdictions' frameworks a
    deployment publishes, never an on/off switch for the ICAAP workspace.
    """
    as_of = date(2026, 12, 31)
    default = frozenset(IcaapSettings.model_fields["frameworks_enabled"].default.split(","))
    assert default == {"bog_icaap"}
    assert (
        registry.applicable("NG", "bank", as_of, cycle_kind="annual", enabled_codes=default) == ()
    )
    assert (
        registry.applicable("KE", "bank", as_of, cycle_kind="annual", enabled_codes=default) == ()
    )
    everything = frozenset(code for code, _ in EXPECTED_DIGESTS)
    for jurisdiction in ("GH", "NG", "KE"):
        offered = registry.applicable(
            jurisdiction, "bank", as_of, cycle_kind="annual", enabled_codes=everything
        )
        assert len(offered) == 1


def test_the_regulator_is_the_one_in_the_jurisdictions_registry() -> None:
    """One name for a supervisor, and it comes from the global registry.

    Read from the migration that seeds it rather than from the hermetic
    fixture, which carries Ghana alone because every fixture bank is Ghanaian.
    """
    spec = util.spec_from_file_location(
        "_jurisdictions_seed",
        Path(__file__).parents[3] / "alembic/versions/202607230017_jurisdictions_registry.py",
    )
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = {row[0]: row[6] for row in module.SEED_ROWS}

    for framework in registry.load_all():
        short = registered.get(framework.jurisdiction)
        assert short is not None, f"{framework.jurisdiction} is not in the jurisdictions registry"
        assert framework.regulator.casefold() == short.casefold()
