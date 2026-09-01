"""Pure credit-domain engines (credit PR-3+).

Loan classification and IFRS 9 ECL live in ``app.domain.capital`` (the capital
run also consumes them); this package holds the engines that exist FOR the
credit module: the standing concentration monitor, and — in later slices —
migration matrices, vintage curves and the advisory PD estimator. Same purity
contract as every domain package: no DB, no I/O, Decimal only, every
regulatory number an argument.
"""
