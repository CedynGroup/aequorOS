"""The Nigeria and Kenya checklists are as complete as the instruments are.

A framework that LOOKS finished but quietly dropped half a regulator's Annex is
the worst possible failure here: a bank would file against it and believe the
report covered what the supervisor asked for. The counts below are therefore
pinned, not as trivia but as the cheapest way to notice that a section, an
attachment or a whole risk category has gone missing from an extraction that
was once complete.

They are counts of what was READ. The provenance of that reading — who read it,
when, and what could not be re-verified afterwards — is recorded in each
framework's ``SOURCES.md`` and is asserted at the bottom of this file.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework


@dataclass(frozen=True)
class Shape:
    """What one published instrument was extracted to."""

    sections: int
    items: int
    risk_categories: int
    attachments: int
    citations: int
    stages: int


#: Nigeria: CBN Revised SRP/ICAAP Guidelines, September 2021, a published final.
#: Sections are Annex B §1 (a)-(n); items come from Annex B §2-§12 and from the
#: numbered paragraphs.
NG = ("cbn_srp_icaap", "2021.09")
NG_SHAPE = Shape(sections=14, items=119, risk_categories=11, attachments=9, citations=159, stages=5)

#: Kenya: CBK Guidance Note on ICAAP, November 2016, a published final. It is a
#: markedly higher-level instrument than either the CBN's or the BoG's, which is
#: why 12 sections carry 69 items where Nigeria's 14 carry 119 — the difference
#: is the text, not the extraction.
KE = ("cbk_icaap_gn", "2016.11")
KE_SHAPE = Shape(sections=12, items=69, risk_categories=14, attachments=4, citations=106, stages=5)

SHAPES = {NG: NG_SHAPE, KE: KE_SHAPE}
IDS = [f"{code} {version}" for code, version in SHAPES]


@pytest.fixture
def ng() -> Framework:
    return registry.get(*NG)


@pytest.fixture
def ke() -> Framework:
    return registry.get(*KE)


@pytest.mark.parametrize(("code", "version"), list(SHAPES), ids=IDS)
def test_the_extraction_is_the_size_it_was_when_it_was_checked(code: str, version: str) -> None:
    framework = registry.get(code, version)
    actual = Shape(
        sections=len(framework.sections),
        items=len(framework.all_items()),
        risk_categories=len(framework.risk_categories),
        attachments=len(framework.attachments),
        citations=len(framework.all_citations()),
        stages=len(framework.stages),
    )
    assert actual == SHAPES[(code, version)]


@pytest.mark.parametrize(("code", "version"), list(SHAPES), ids=IDS)
def test_every_item_is_a_cited_paraphrase(code: str, version: str) -> None:
    """The rule the whole build rests on: nothing is invented, nothing quoted."""
    framework = registry.get(code, version)
    items = framework.all_items()
    assert len({item.id for item in items}) == len(items)
    for item in items:
        assert item.citations, item.id
        assert item.text.strip()
        assert len(item.text) <= 280, item.id
        assert '"' not in item.text, item.id


@pytest.mark.parametrize(("code", "version"), list(SHAPES), ids=IDS)
def test_the_report_goes_to_the_board_and_exactly_one_stage_freezes_it(
    code: str, version: str
) -> None:
    framework = registry.get(code, version)
    assert [stage.seq for stage in framework.stages] == list(range(1, len(framework.stages) + 1))
    freezing = [stage for stage in framework.stages if stage.freeze_on_approve]
    assert len(freezing) == 1
    assert freezing[0].decision == "approve"
    assert framework.stages[-1].decision == "attest"
    assert framework.stages_source_status == "platform_default"


@pytest.mark.parametrize(("code", "version"), list(SHAPES), ids=IDS)
def test_the_instrument_applies_to_banks(code: str, version: str) -> None:
    """Both address licensed banks. SDIs never see ICAAP at all (D-020)."""
    framework = registry.get(code, version)
    assert framework.institution_classes == ("bank",)
    assert framework.deadline.as_of == "fy_end"
    assert framework.deadline.fy_end_month_day == "12-31"


def test_nigeria_requires_the_board_resolution_at_submission(ng: Framework) -> None:
    """What the CBN demands OF THE PACKAGE, as gates rather than as prose."""
    resolution = ng.attachment("board_resolution")
    assert (resolution.gate, resolution.min_count) == ("submission", 1)
    assert resolution.relaxable_by_signing_policy is False
    for kind in ("senior_management_report", "board_committee_minutes", "board_approved_ras"):
        attachment = ng.attachment(kind)
        assert attachment.gate == "freeze"
        assert attachment.min_count == 1
        assert attachment.relaxable_by_signing_policy is False


def test_nigerias_conditional_attachments_state_their_condition(ng: Framework) -> None:
    """An attachment that is not always required says when it is.

    The CBN may call for an ICAAP update at any time; the letter that does so
    belongs to the package only on such a cycle. A group member's parent
    support evidence is the same shape of rule.
    """
    assert ng.attachment("regulator_request_letter").applies_when == "cycle_kind:regulator_request"
    assert ng.attachment("parent_support_evidence").applies_when == "group_member"


def test_kenya_prescribes_no_attachment_at_all(ke: Framework) -> None:
    """The Guidance Note names no document to attach, so none is gated.

    Inventing a board resolution requirement here because Ghana and Nigeria
    both have one is exactly the copying this build refuses. The evidence a
    Kenyan bank does hold is offered as optional.
    """
    gates = {attachment.gate for attachment in ke.attachments}
    assert gates <= {"optional", "per_block"}
    assert all(attachment.min_count == 0 for attachment in ke.attachments)
    assert {attachment.kind for attachment in ke.attachments} == {
        "board_approval_evidence",
        "independent_review_report",
        "capital_management_policy",
        "manual_table_evidence",
    }
    assert any(note.code == "no_board_resolution_prescribed" for note in ke.notes)


def test_each_regulators_risk_taxonomy_is_its_own(ng: Framework, ke: Framework) -> None:
    """Annex A, as printed, including the risks the others do not name.

    Nigeria names environmental and social risk and model risk; Kenya names
    residual, securitisation, compliance-and-disclosure and external-factor
    risk, and treats liquidity as a risk to assess rather than capitalise.
    Neither list is the other's, and neither is Ghana's.
    """
    assert [category.key for category in ng.risk_categories] == [
        "credit",
        "market",
        "operational",
        "concentration",
        "irrbb",
        "business_strategic",
        "reputational",
        "country_transfer",
        "model",
        "environmental_social",
        "other_material",
    ]
    assert [category.key for category in ke.risk_categories] == [
        "credit",
        "market",
        "operational",
        "liquidity",
        "concentration",
        "irrbb",
        "residual",
        "securitisation",
        "business_strategic",
        "reputational",
        "market_liquidity",
        "compliance_disclosure",
        "external_factors",
        "other_material",
    ]
    for framework in (ng, ke):
        assert [category.number for category in framework.risk_categories] == list(
            range(1, len(framework.risk_categories) + 1)
        )
        for category in framework.risk_categories:
            assert category.components, category.key


def test_nigeria_records_that_the_annex_prints_one_heading_twice(ng: Framework) -> None:
    """An oddity in the source is written down, not silently normalised."""
    assert any(note.code == "annex_b_numbering" for note in ng.notes)
    refs = {citation.ref for citation in ng.all_citations()}
    # Both uses of heading 6 are cited, and the second carries its printed page
    # in the ref so a reader can tell which one an item came from.
    assert any(ref.startswith("AnnexB-6p20") for ref in refs)
    assert any(ref.startswith("AnnexB-6(") for ref in refs)


@pytest.mark.parametrize(("code", "version"), list(SHAPES), ids=IDS)
def test_the_sources_manifest_says_what_was_not_re_verified(code: str, version: str) -> None:
    """Honesty about provenance is part of the deliverable, not a footnote.

    The PDFs behind these two frameworks are not committed to the repository.
    The fingerprints in the manifest identify the files that were read; nothing
    in this suite re-hashes them, and the manifest has to say so rather than
    let a reader take a 64-character digest for a check that ran.
    """
    framework = registry.get(code, version)
    manifest = registry.FRAMEWORK_ROOT / framework.jurisdiction.lower() / "SOURCES.md"
    text = manifest.read_text(encoding="utf-8")
    assert "## Provenance of this manifest" in text
    assert "cannot be re-verified from a checkout" in text
