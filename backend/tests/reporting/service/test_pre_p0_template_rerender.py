"""Re-exporting a pre-P0 package gives the same bytes as before (decision D-021).

ICAAP P0 changed the wording of six templates. A package generated BEFORE that
must re-export exactly as it did, signed or not — the rule "existing sealed
packages stay untouched" covers the artifact, not only the snapshot.

The fixtures under ``tests/fixtures/pre_p0_packages/`` were produced by the
BASELINE code (``ca294f04``, the commit before P0) over the deterministic
canonical book: each holds a real generated package (snapshot, source runs,
identity) and the SHA-256 of the PDF, XLSX, CSV (and, where the return has one,
the working XLSX) the baseline renderer produced for it. This test stores each
package in the database as it would exist today and re-exports it through the
production export path; every artifact must hash identically.

Regenerating a fixture is only ever done from a checkout of the baseline
commit, never from the current code — the hashes are the historical record.

This module's other-suite cases live in

``test_pre_p0_template_rerender_integration.py``.
"""

from __future__ import annotations

__all__ = ["_fixture"]
import json
from pathlib import Path
from typing import Any

import pytest

from app.reporting.service.registry import get_definition
from app.reporting.service.templates import (
    TEMPLATE_REVISIONS,
    build_rendered_return,
    get_template,
    template_for_snapshot,
)
from app.reporting.service.templates_legacy import LEGACY_TEMPLATES

FIXTURES = Path(__file__).parents[2] / "fixtures" / "pre_p0_packages"
CODES = ("CAR-RWA", "IRRBB-PILOT", "ICAAP-STRESS-APPENDIX2", "SDI-STRESS-ANNUAL", "STRESS-PACK")


def _fixture(code: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{code}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", CODES)
def test_a_pre_p0_snapshot_selects_the_frozen_text(code: str) -> None:
    record = _fixture(code)
    definition = get_definition(code)
    assert definition is not None
    current = get_template(definition.template_id)
    assert current is not None
    assert definition.template_id in TEMPLATE_REVISIONS
    frozen = template_for_snapshot(current, record["snapshot"])
    assert frozen is LEGACY_TEMPLATES[definition.template_id]
    assert frozen is not current
    rendered = build_rendered_return(
        current,
        record["snapshot"],
        record["source_runs"],
        package_id=record["package_id"],
        package_version=record["version"],
    )
    assert rendered.template is frozen
    assert rendered.report_notes == ()


def test_a_new_snapshot_selects_the_current_text() -> None:
    record = _fixture("ICAAP-STRESS-APPENDIX2")
    snapshot = dict(record["snapshot"])
    metadata = dict(snapshot["metadata"])
    current = get_template("bog-icaap-stress-appendix2-v1")
    assert current is not None
    # Stamped but without the governed-minimum provenance its neutral headers
    # rely on: still the frozen text (it would otherwise state no minimum).
    metadata["template_revision"] = TEMPLATE_REVISIONS["bog-icaap-stress-appendix2-v1"]
    snapshot["metadata"] = metadata
    assert template_for_snapshot(current, snapshot) is not current
    metadata["parameter_provenance"] = []
    assert template_for_snapshot(current, snapshot) is current
    # A template P0 never revised is always its own text.
    lmt = get_template("bog-lmt-liquidity-v1")
    assert lmt is not None
    assert template_for_snapshot(lmt, {"metadata": {}}) is lmt
