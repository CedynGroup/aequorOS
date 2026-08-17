"""Bank of Ghana official prudential returns (BSD1 … BSD17).

Template-faithful generation of every official return under ``docs/reporting/``:
the committed ``layouts/*.json`` ARE the official sheet structures (every cell,
label, input, formula, merge, width); ``linemaps/`` map each input cell to a
named AequorOS source; ``engine`` fills the inputs and evaluates the templates'
OWN formulas (roll-ups are BoG's by construction); ``render`` rebuilds the
workbook from the layout and writes a values-only, sealed export.

Registry + build plan: ``docs/bog_returns/00_full_return_registry.md``.
"""
