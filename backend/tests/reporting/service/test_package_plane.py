"""The package plane P3 adds: attachments, family hooks, and the filing set.

Four properties this suite exists to keep true:

1. ``return_signing_policies.required_attachments`` is ENFORCED, for every
   family, and the e-sign kill switch does not exempt it. Before P3 a policy
   could name a Board resolution and nothing could satisfy it, so the
   requirement silently passed on every submission (audit C-6).
2. What a filing carries is decided by an explicit opt-in, kind by kind. The
   recalculable Excel copy IS filed (founder decision 2026-09-20 — supervisors
   prefer the form with its formulas live); the recalculable Word draft is not,
   and a working kind that names itself nowhere is excluded by default.
3. The family hook seam dispatches, and a family with no hook changes nothing —
   which is what makes "ICAAP got its own rules" safe for every other return.
4. The calendar and the anchor picker say honest things about a return that is
   not in force yet (D-058).

This module's other-suite cases live in ``test_package_plane_integration.py``.
"""

from __future__ import annotations

from app.reporting.service import workflow as reporting_workflow
from app.reporting.service.registry import REGISTRY


def test_a_working_kind_reaches_a_regulator_only_by_opting_in_on_both_axes() -> None:
    """The safety property the single set used to give by excluding everything.

    ``WORKING_ARTIFACT_KINDS`` still names every recalculable artifact; what a
    filing excludes is DERIVED from it by subtracting the explicit opt-in, so a
    working kind added to the first set and forgotten is excluded, not filed.
    The generator map is the second axis and is deny-by-default in the same
    way. Deleting either derivation and filtering on a literal would silently
    re-admit the next one.
    """
    filable = reporting_workflow.FILABLE_WORKING_ARTIFACT_KINDS
    working = reporting_workflow.WORKING_ARTIFACT_KINDS
    assert filable <= working, "a filable working kind must still be a working kind"
    assert filable == frozenset({"xlsx_working"}), "one kind opted in, and only one"
    unfilable = reporting_workflow.UNFILABLE_WORKING_ARTIFACT_KINDS
    assert unfilable == working - filable, "the exclusion must stay derived, not written out"
    assert "docx_working" in unfilable
    # a hypothetical third kind is excluded by construction, not by memory
    assert "pptx_working" not in filable

    admits = reporting_workflow.filing_admits_artifact
    # ordinary artifacts are unaffected by either axis
    for kind in ("pdf", "xlsx", "csv"):
        assert admits(kind, generator="bog_form")
        assert admits(kind, generator="sdi_lmt")
        assert admits(kind, generator=None)
    # the formula workbook: BoG's own generator only
    assert admits("xlsx_working", generator="bog_form")
    assert not admits("xlsx_working", generator="sdi_lmt")
    assert not admits("xlsx_working", generator="icaap_report")
    assert not admits("xlsx_working", generator=None), "an unregistered return files no copy"
    # the Word draft: no generator can file it, because its KIND never opted in
    for generator in ("bog_form", "icaap_report", None):
        assert not admits("docx_working", generator=generator)
    # a hypothetical future kind: opting in by kind alone is not enough
    generators = reporting_workflow.WORKING_ARTIFACT_FILING_GENERATORS
    assert generators == {"xlsx_working": frozenset({"bog_form"})}
    assert set(generators) <= filable, "only an opted-in kind may name generators"


def test_every_icaap_entry_pairs_with_its_own_template() -> None:
    """D-051's open gap: ICAAP-UPDATE cannot share ICAAP-REPORT's template id."""
    from app.reporting.service.templates import get_template  # noqa: PLC0415

    ids = set()
    for code, definition in REGISTRY.items():
        if definition.family != "icaap":
            continue
        template = get_template(definition.template_id)
        assert template is not None, code
        assert template.return_code == code
        assert definition.template_id not in ids, "two ICAAP returns share a template id"
        ids.add(definition.template_id)
    assert len(ids) == 3


def test_each_signing_role_maps_to_the_permission_it_actually_exercises() -> None:
    """A preparer FINISHES the document; a checker APPROVES the filing."""
    from app.core.authorization import Permission  # noqa: PLC0415
    from app.reporting.service import family_access  # noqa: PLC0415

    assert family_access.CERTIFY_PERMISSIONS["preparer"] is Permission.EDIT
    assert family_access.CERTIFY_PERMISSIONS["approver"] is Permission.APPROVE
    assert family_access.CERTIFY_PERMISSIONS["board"] is Permission.APPROVE


def test_every_family_names_whole_institution_authority() -> None:
    from app.core.authorization import Module, Sensitivity  # noqa: PLC0415
    from app.reporting.models.packages import RETURN_FAMILIES  # noqa: PLC0415
    from app.reporting.service import family_access  # noqa: PLC0415

    for family in RETURN_FAMILIES:
        gate = family_access.gate_for(family)
        assert gate.module is (Module.CAPITAL if family == "icaap" else Module.REGULATORY)
        assert gate.sensitivity is (
            Sensitivity.CONFIDENTIAL if family == "icaap" else Sensitivity.RESTRICTED
        )
