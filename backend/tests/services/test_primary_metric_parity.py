"""The headline metric per live module is declared in THREE places.

``window_analytics._PRIMARY_METRIC_KEY`` (the daily aggregates), the dashboard's
``components/live/moduleDisplay.ts`` ``PRIMARY_METRIC`` (the pulse card, the
live status card, the board pack) and ``components/home/WindowAnalysis.tsx``
``METRIC_LABELS`` (how the daily aggregates are labelled). Until 2026-09-21 the
IRR entry named ``eve_limit_pct`` — the LIMIT, a constant — in all three, so the
pulse sparkline was flat and the window statistics summarised a parameter.

Read out of the dashboard's own sources rather than duplicated here (precedent:
``test_attestation_typed_fonts.py``), so a key changed on one side and forgotten
on the other fails in CI instead of in front of a Treasurer.

The same reading also holds the dashboard's ``ADVISORY_HEADLINE_MODULES`` to the
authority registry: a headline metric that is filed for no institution class may
not be presented as a certified figure (BI decision D-022).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.authority.registry import REGISTRY, AdvisoryDesignation
from app.models.live import LIVE_MODULES
from app.services.window_analytics import _PRIMARY_METRIC_KEY  # noqa: PLC2701 - the map under test

_DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard" / "components"
_MODULE_DISPLAY = _DASHBOARD / "live" / "moduleDisplay.ts"
_WINDOW_ANALYSIS = _DASHBOARD / "home" / "WindowAnalysis.tsx"

#: ``  irr: { key: "worst_eve_change_pct_tier1", label: "ΔEVE / Tier 1" },``
_TS_PRIMARY_ENTRY = re.compile(r"^\s*(\w+):\s*\{\s*key:\s*[\"']([^\"']+)[\"']", re.MULTILINE)
#: ``  lcr_pct: 'LCR',``
_TS_LABEL_ENTRY = re.compile(r"^\s*(\w+):\s*[\"'][^\"']+[\"']\s*,?\s*$", re.MULTILINE)
#: ``  new Set<LiveModule>(["ftp", "rating", "forecast"]);``
_TS_MODULE_STRING = re.compile(r"[\"'](\w+)[\"']")


def _block(source: str, declaration: str) -> str:
    """The object literal following ``declaration`` up to its closing ``};``."""
    start = source.index(declaration)
    end = source.index("};", start)
    return source[start:end]


def _dashboard_primary_metric() -> dict[str, str]:
    source = _MODULE_DISPLAY.read_text(encoding="utf-8")
    block = _block(source, "const PRIMARY_METRIC")
    found = {module: key for module, key in _TS_PRIMARY_ENTRY.findall(block)}
    assert found, "PRIMARY_METRIC was not found in moduleDisplay.ts — the regex or the file moved"
    return found


def _dashboard_advisory_modules() -> set[str]:
    """``ADVISORY_HEADLINE_MODULES`` in ``moduleDisplay.ts`` — a ``Set`` literal,
    so it ends at its ``]`` rather than at a ``};``."""
    source = _MODULE_DISPLAY.read_text(encoding="utf-8")
    start = source.index("const ADVISORY_HEADLINE_MODULES")
    found = set(_TS_MODULE_STRING.findall(source[start : source.index("]", start)]))
    assert found, (
        "ADVISORY_HEADLINE_MODULES was not found in moduleDisplay.ts — the regex or the file moved"
    )
    return found


def _window_metric_labels() -> set[str]:
    source = _WINDOW_ANALYSIS.read_text(encoding="utf-8")
    block = _block(source, "const METRIC_LABELS")
    found = {key for key in _TS_LABEL_ENTRY.findall(block)}
    assert found, "METRIC_LABELS was not found in WindowAnalysis.tsx — the regex or the file moved"
    return found


def test_the_backend_headline_key_is_the_dashboards_for_every_module() -> None:
    dashboard = _dashboard_primary_metric()
    for module, key in _PRIMARY_METRIC_KEY.items():
        assert module in dashboard, f"{module} has no PRIMARY_METRIC entry in moduleDisplay.ts"
        assert dashboard[module] == key, (
            f"{module}: backend aggregates {key!r} but the dashboard headlines "
            f"{dashboard[module]!r}"
        )


def test_the_window_analysis_panel_labels_every_daily_key() -> None:
    labels = _window_metric_labels()
    for module, key in _PRIMARY_METRIC_KEY.items():
        assert key in labels, f"{module}'s daily key {key!r} has no label in WindowAnalysis.tsx"


def test_the_irr_headline_is_the_measured_change_not_the_limit() -> None:
    """The limit is a parameter; only a registered metric may be a headline."""
    assert _PRIMARY_METRIC_KEY["irr"] == "worst_eve_change_pct_tier1"
    assert "eve_limit_pct" not in _PRIMARY_METRIC_KEY.values()
    assert _PRIMARY_METRIC_KEY["credit"] == "npl_ratio_pct"


def test_every_live_module_has_a_headline_metric() -> None:
    """A module missing from the map is silently absent from window analytics.

    The rating ladder was: ``_daily_stats`` skips a row whose module has no key,
    so a Treasurer opening any window saw seven engines and never learnt the
    eighth had computed (decision D-031). Advisory standing is a labelling rule,
    not grounds for omission.
    """
    assert set(_PRIMARY_METRIC_KEY) == set(LIVE_MODULES)


def test_a_headline_metric_that_is_never_filed_is_labelled_advisory() -> None:
    """Only a filed metric may read as a certified figure (BI decision D-022).

    The authority registry decides: FTP's portfolio NIM and the rating engine's
    PD band are ``advisory_only`` and the five-year projected CAR is
    ``supervisory_monitoring``, so the window-analysis panel marks those three
    lines. The dashboard set is a MIRROR of the registry, pinned here so a metric
    that loses its filed standing cannot go on reading as certified — and so a
    filed metric is never disclaimed away.
    """
    advisory = _dashboard_advisory_modules()
    for module, key in _PRIMARY_METRIC_KEY.items():
        entries = REGISTRY.for_metric(key)
        assert entries, f"{module}'s headline {key!r} is in no authority-registry entry"
        filed = any(entry.advisory_designation is AdvisoryDesignation.FILED for entry in entries)
        if filed:
            assert module not in advisory, (
                f"{module} headlines {key!r}, which IS filed for at least one institution "
                "class — labelling it advisory understates it"
            )
        else:
            assert module in advisory, (
                f"{module} headlines {key!r}, which is filed for no institution class — "
                "add it to ADVISORY_HEADLINE_MODULES in moduleDisplay.ts"
            )


def test_daily_rows_follow_the_live_module_order() -> None:
    """``_daily_stats`` iterates the map, so its order IS the wire order."""
    ordered = [module for module in LIVE_MODULES if module in _PRIMARY_METRIC_KEY]
    assert list(_PRIMARY_METRIC_KEY) == ordered
