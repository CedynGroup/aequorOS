"""Pure quantitative curve library (spec: AequorOS Market Data & Curve Platform §5-§7).

Numerical heart of the market-data platform: day-count/quote conventions,
zero-curve interpolation (including Hagan-West monotone convex), NSS parametric
fitting, sovereign bootstrap from Ghana's bill/bond reality, forward derivation
with the pre-publish QA gate, the MPR-anchored meeting-date step curve, and the
Engle-Granger machinery behind the synthetic discounting level.

Unlike the accounting-style domain engines (IRR, FTP), this package is
float64/numpy end to end: every routine is iterative numerical analysis
(Newton, least squares, spline construction) where ``Decimal`` buys nothing and
costs interoperability with numpy/scipy. The ``Decimal`` boundary sits at the
calling service, which quantizes *published* node values; inside this package
determinism comes from fixed algorithms, fixed iteration rules and zero
randomness (no random seeds anywhere in library code).
"""
