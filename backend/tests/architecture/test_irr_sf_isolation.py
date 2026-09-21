"""The Standardised Framework and the legacy IRRBB engine must not meet.

P5-DESIGN §1.7. Every existing IRRBB return — the quarterly pilot, the SDI
return, the enterprise-stress overlay, the implied rating — is produced by
``app/domain/irr/engine.py``, and its goldens are filed evidence. The framework
is therefore a SIBLING: a new module value on the same immutable run table,
reached through its own service, sharing nothing but the Tier 1 denominator.

Two import directions are pinned here, both by AST so a string mention cannot
satisfy them:

* no Standardised Framework module imports the legacy engine, and the legacy
  engine imports nothing named ``standardised`` — so a change to one cannot
  move the other's numbers;
* the live plane never reaches the framework's service. The framework mints
  immutable filing evidence on request; it is not polled, it writes no
  ``live_metrics``, and a live tick that imported it would start doing filing
  work on a debounce.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: The pure framework modules (workstream A) and its service (workstream B).
SF_MODULES: tuple[Path, ...] = (
    APP / "domain" / "irr" / "standardised.py",
    APP / "domain" / "irr" / "standardised_cash_flows.py",
    APP / "domain" / "irr" / "standardised_params.py",
    APP / "services" / "regulatory_irr_sf.py",
)
LEGACY_ENGINE = APP / "domain" / "irr" / "engine.py"

#: Modules that run on the LIVE tier, or that every module's freshness and
#: rating paths go through. None of them may reach the framework.
LIVE_PLANE: tuple[Path, ...] = (
    APP / "services" / "pipeline.py",
    APP / "services" / "freshness.py",
    APP / "services" / "implied_rating.py",
    APP / "services" / "regulatory_reporting" / "generation.py",
)

SF_SERVICE_MODULE = "app.services.regulatory_irr_sf"


def _imported_names(path: Path) -> set[str]:
    """Every module name the file imports, dotted and fully resolved."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_framework_never_imports_the_legacy_engine_arithmetic() -> None:
    """The one seam is Tier 1, and it is a named public function."""
    offenders: dict[str, set[str]] = {}
    for path in SF_MODULES:
        engine_imports = {
            name
            for name in _imported_names(path)
            if name.endswith("irr.engine") or ".irr.engine." in name
        }
        if engine_imports:
            offenders[path.name] = engine_imports
    assert offenders == {}, (
        "the Standardised Framework must not reach into the legacy IRRBB engine — "
        "its goldens are filed evidence: " + repr(offenders)
    )


def test_the_legacy_engine_never_imports_the_framework() -> None:
    names = _imported_names(LEGACY_ENGINE)
    assert not any("standardised" in name for name in names), (
        "app/domain/irr/engine.py must stay byte-identical to its pre-P5 form; "
        f"it now imports {sorted(name for name in names if 'standardised' in name)}"
    )


def test_the_framework_service_borrows_only_the_tier_one_denominator() -> None:
    """The ΔEVE denominator is shared on purpose; nothing else is.

    Two denominators that could disagree is the defect this prevents, so the
    import is expected — but it must be the ONE public seam, not a reach into
    the legacy service's internals.
    """
    names = _imported_names(APP / "services" / "regulatory_irr_sf.py")
    legacy = {name for name in names if "services.regulatory_irr" in name}
    assert legacy == {
        "app.services.regulatory_irr",
        "app.services.regulatory_irr.tier1_for_period",
    }, sorted(legacy)


def test_the_live_plane_never_reaches_the_framework_service() -> None:
    """The framework is not a live module: no tick, no debounce, no poll."""
    offenders = [
        path.name
        for path in LIVE_PLANE
        if path.exists()
        and any(name.startswith(SF_SERVICE_MODULE) for name in _imported_names(path))
    ]
    assert offenders == [], (
        "the Standardised Framework mints immutable filing evidence on request; "
        f"these live-plane modules import it: {offenders}"
    )


def test_the_framework_is_not_registered_as_a_live_module() -> None:
    from app.models.live import LIVE_MODULES  # noqa: PLC0415 - narrow, local assertion
    from app.services.regulatory_irr_sf import MODULE_IRR_SF  # noqa: PLC0415

    assert MODULE_IRR_SF not in LIVE_MODULES


#: Every public callable the framework service is allowed to expose. A LIVE
#: entry point would be a new name here, whatever it is called.
SF_SERVICE_PUBLIC_API: frozenset[str] = frozenset(
    {
        "SfMandate",
        "SfRunError",
        "get_standardised_framework",
        "get_standardised_framework_attempts",
        "latest_sf_attempt",
        "latest_sf_run",
        "parameter_value_text",
        "post_shock_floor_statement",
        "refusal_sentence",
        "run_standardised_framework",
        "sf_mandate",
    }
)


def test_the_framework_service_has_no_live_computation_entry_point() -> None:
    """The public surface is pinned, so a live entry point cannot be added quietly.

    Until 2026-09-20 this read ``assert not hasattr(module, "compute_live")``
    — a name no one has ever written, which could only fail if somebody chose
    that exact spelling. Pinning the surface instead means ANY new public
    callable fails here and has to be justified in this list; the live plane's
    own entry points are pinned separately, by import, above.
    """
    from app.services import regulatory_irr_sf  # noqa: PLC0415 - narrow, local assertion

    public = {
        name
        for name in dir(regulatory_irr_sf)
        if not name.startswith("_")
        and callable(getattr(regulatory_irr_sf, name))
        and getattr(getattr(regulatory_irr_sf, name), "__module__", "")
        == regulatory_irr_sf.__name__
    }
    assert public == set(SF_SERVICE_PUBLIC_API), (
        "the framework service's public surface moved. A filing entry point is "
        "fine — add it here. A LIVE one (a tick, a debounce, a recompute-now) "
        "is what this test exists to stop: the framework mints immutable "
        f"evidence on request. Added: {sorted(public - SF_SERVICE_PUBLIC_API)}; "
        f"removed: {sorted(SF_SERVICE_PUBLIC_API - public)}"
    )
