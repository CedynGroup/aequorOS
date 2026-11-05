"""Tests for test_regulatory_reporting_workflow."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import RegulatoryPackage
from app.reporting.service import workflow


def test_allowed_transition_table_blocks_illegal_moves() -> None:
    package = RegulatoryPackage(status="generated")
    workflow.ensure_transition_allowed(package, "validated")  # legal, no raise

    for illegal in ("approved", "submitted", "acknowledged", "draft"):
        with pytest.raises(HTTPException) as exc_info:
            workflow.ensure_transition_allowed(package, illegal)
        assert exc_info.value.status_code == 409

    for terminal in ("acknowledged", "superseded"):
        package.status = terminal
        for target in ("generated", "validated", "submitted"):
            with pytest.raises(HTTPException):
                workflow.ensure_transition_allowed(package, target)

    package.status = "submitted"
    workflow.ensure_transition_allowed(package, "acknowledged")
    workflow.ensure_transition_allowed(package, "rejected")
    with pytest.raises(HTTPException):
        workflow.ensure_transition_allowed(package, "generated")
