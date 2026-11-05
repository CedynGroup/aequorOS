"""Validate filename conventions against pytest's authoritative collected suites."""

from __future__ import annotations

import pytest

SUITES = frozenset({"unit", "integration", "e2e"})


class SuiteFilenameGuard:
    """Inspect complete collection after suite assignment, without a membership registry."""

    def pytest_configure(self, config: pytest.Config) -> None:
        """Declare markers for standalone probes without assigning any test's suite."""
        for suite in sorted(SUITES):
            config.addinivalue_line("markers", f"{suite}: test suite assignment")

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Reject unassigned, mixed, and filename-mismatched modules after collection."""
        suites_by_module: dict[str, set[str]] = {}
        for item in session.items:
            suites = {mark.name for mark in item.iter_markers() if mark.name in SUITES}
            suites_by_module.setdefault(item.nodeid.split("::", 1)[0], set()).update(
                suites or {"<unassigned>"}
            )
        violations: list[str] = []
        for module, suites in sorted(suites_by_module.items()):
            expected = (
                "integration"
                if module.endswith("_integration.py")
                else "e2e"
                if module.endswith("_e2e.py")
                else "unit"
            )
            if suites != {expected} or module.endswith("_unit.py"):
                violations.append(
                    f"{module}: expected exactly one {expected} suite"
                    + (" with a suffix-free filename" if expected == "unit" else "")
                    + f"; actual suites: {', '.join(sorted(suites))}"
                )
        if violations:
            raise pytest.UsageError("\n".join(violations))
        session.config.get_terminal_writer().line(
            f"Suite filenames verified for {len(session.items)} collected tests"
        )
