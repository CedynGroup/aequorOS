"""Cross-engine equivalence suite (forensic audit 2026-08-21, §10 item 2).

The audits found four executable calculation planes and no automated control
proving that two engines which claim to produce the same number actually do.
This package is that control. It answers exactly one question per test:

    when two code paths claim to compute the same figure, do they?

Three rules govern everything here.

1. **Equality is asserted only where equality is claimed.** A test that forces
   two legitimately different methodologies to agree is worse than no test: it
   invites someone to "fix" a correct engine. Where the methodologies genuinely
   differ, the assertion is on the DECLARED divergence — that both sides are
   registered in :mod:`app.domain.authority.registry`, that the alternate
   carries a ``MethodologyDivergence``, and that the difference has the
   documented sign. See ``test_declared_divergences.py``.
2. **Tolerances are declared, never discovered.** A tolerance exists because a
   transformation legitimately rounds (a package storing a percentage at fewer
   decimal places, a template presenting thousands). It never exists because
   the numbers "came out close".
3. **A moved number is a finding, not a golden to re-baseline.** If an
   assertion here fails, the economics changed. Explain it or fix it; do not
   loosen the assertion.

Scope, and the sibling gate
---------------------------

This package covers the CALCULATION-PLANE overlaps: forecast year 0 against the
capital and liquidity runs, and each run-backed RETURN PACKAGE against the run
it sealed. ``tests/services/test_reporting_equivalence.py`` (WS-D) covers the
other half — every BoG form CELL bound to an engine-backed resolver, against
the source run's persisted line items. ``test_declared_divergences.py`` holds
the ledger that says which claims either gate actually proves.

Nothing here equates two methodologies. Where the forecast is asserted equal to
the capital run, it is because BOTH sides call
``app.domain.capital.engine.compute_capital_ratios`` — one engine, one method,
previously handed two different fact sets. Aligning an input scope is not
merging two regulatory methods, and no engine's arithmetic was changed to make
two numbers agree.
"""
