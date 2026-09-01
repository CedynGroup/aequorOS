"""Which calculation modules an institution's regime actually runs.

THE ONE AUTHORITY for "does this module apply to this institution?", shared by
both computation tiers — the live plane (``pipeline._scoped_modules``) and the
official filing plane (``data_activation._run_all_modules``).

Why this module exists (founder review, 2026-08-23)
---------------------------------------------------
The knowledge was already here and correct — it just lived in ``pipeline.py``
and was applied to the live tier only. The official tier ran a hardcoded tuple
of seven modules for every institution regardless of licence class, so a
savings-&-loans tenant's official run attempted:

- ``liquidity`` — the **Basel LCR/NSFR** engine. BoG supervises an SDI's
  liquidity through the LMTD monitoring tools, not LCR/NSFR; the live tier has
  excluded it for SDIs since docs/sdi.md, and the SDI's own Table-1 view is a
  separate, working module.
- ``fx`` and ``ftp`` — **bank-only modules**. Neither is in
  ``institution_types.SDI_MODULES``; an SDI is not entitled to them at all.
- ``forecast`` — the five-year projection measures an institution against Basel
  CET1/Tier 1/CAR/leverage and LCR/NSFR. ``sdi_regime`` declares no projection
  authority for the s.29 regime, so it refuses by design.

All four then reported ``failed`` with parameter or policy refusals, which read
as "this tenant is broken" rather than "these modules do not apply to it". They
also meant the run produced **zero succeeded modules**, so nothing could be
published and no return could be generated — the founder-visible symptom.

The fix is not to seed the missing parameters. Seeding ``lcr_min``/``nsfr_min``
for an SDI would assert a Basel floor BoG never imposed on it, which is exactly
the fail-open this codebase refuses everywhere else. The fix is to stop running
the module.

Three independent gates, all data-driven
----------------------------------------
1. **Entitlement** — is the module in the licence class's ``default_modules``?
   (``institution_types``, fail-closed.)
2. **Metric authority** — does this institution's class have authority for the
   module's metric family? (``sdi_regime`` over WS-A's registry, so the tiers
   can never disagree with the registry about who a metric belongs to.)
3. **Regime-specific engine substitution** — a module the class IS entitled to
   but which the class runs through a DIFFERENT engine. Today that is exactly
   one case: SDI liquidity is LMTD/Reserve, not Basel LCR/NSFR.

A universal bank passes all three for every module, so this is byte-identical
for banks — pinned by ``tests/services/test_module_scope.py``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.authority.registry import MetricFamily
from app.models import Bank
from app.services import institution_types, sdi_regime

#: Calculation-module key → the ``institution_types.default_modules`` slug that
#: entitles it (docs/sdi.md §3.2). A module absent from the tenant's set is not
#: computed — an SDI does not run FX/FTP, so no empty FX/FTP live-metric,
#: finding, or official run reaches it.
MODULE_SCOPE_KEY: dict[str, str] = {
    "liquidity": "liquidity",
    "capital": "capital",
    "irr": "irrbb",
    "fx": "fx",
    "ftp": "ftp",
    "rating": "markets",
    "credit": "credit",
    "implied_rating": "markets",
    "forecast": "forecasting",
}

#: Modules whose outputs are authoritative only for a declared institution class.
#: The declaration lives in WS-A's metric authority registry; this maps the
#: module key onto the metric family to look up there.
MODULE_METRIC_FAMILY: dict[str, MetricFamily] = {
    "forecast": MetricFamily.FORECAST,
    "credit": MetricFamily.CREDIT,
}

#: (module, institution_class) pairs where the class is entitled to the module
#: but runs it through a DIFFERENT engine than the one keyed here, so this
#: engine must not run. Kept as explicit data rather than an ``if`` so the
#: exception is enumerable and testable.
#:
#: ``("liquidity", "sdi")``: an SDI's liquidity is the canonical LMTD / Reserve
#: view read directly from the position book (``sdi_readiness.liquidity_table1``,
#: ``sdi_views``), not Basel LCR/NSFR. BoG has published no LCR or NSFR
#: requirement for any class, and the LMTD Table 1 ratios that DO bind an SDI
#: are governed in the control plane (``broad_to_*``, ``narrow_to_*``,
#: ``primary_liquidity_reserve_pct``, ``secondary_liquidity_reserve_pct``).
ENGINE_SUBSTITUTED: frozenset[tuple[str, str]] = frozenset({("liquidity", "sdi")})


def runs_module(db: Session, bank: Bank, module: str) -> bool:
    """Whether ``bank``'s regime runs ``module`` at all.

    Fail-closed through :func:`institution_types.get_type`: an institution whose
    licence class does not resolve raises rather than being treated as a bank.
    """
    institution_type = institution_types.get_type(db, bank)
    if MODULE_SCOPE_KEY.get(module, module) not in set(institution_type.default_modules):
        return False
    if (module, institution_type.institution_class) in ENGINE_SUBSTITUTED:
        return False
    family = MODULE_METRIC_FAMILY.get(module)
    if family is None:
        return True
    return sdi_regime.family_has_authority(sdi_regime.institution_class_enum(db, bank), family)


def out_of_regime_note(db: Session, bank: Bank, module: str) -> str:
    """Why ``module`` is not run for this institution, in words for an operator.

    A module that does not apply must not be reported as a FAILURE — that is
    what made an SDI's official run look like a broken tenant. It is reported as
    out-of-scope, with the reason.
    """
    institution_type = institution_types.get_type(db, bank)
    klass = institution_type.institution_class
    label = institution_type.display_name
    if MODULE_SCOPE_KEY.get(module, module) not in set(institution_type.default_modules):
        return (
            f"The {module} module is not part of the {label} licence class's module set, "
            "so no run is produced for it."
        )
    if (module, klass) in ENGINE_SUBSTITUTED:
        return (
            "Liquidity for a specialised deposit-taking institution is supervised through "
            "the Bank of Ghana liquidity monitoring tools (LMTD Table 1 ratios and the "
            "primary/secondary reserve requirements), not the Basel LCR and NSFR. The "
            "Basel engine is not run; the Table-1 view is this institution's liquidity "
            "position."
        )
    return (
        f"No {module} method is registered for the {label} regime, so no run is produced. "
        "Nothing is substituted from another licence class."
    )
