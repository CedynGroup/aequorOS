"""General-ledger domain rules shared by the filed returns and the BI plane.

``pl_mapping`` is the pure half of the BSD7 P&L resolver
(``app/services/regulatory_reporting/bog_forms/sources_ext/bsd7.py``): fiscal
windows, the chart-of-accounts → official-item mapping, and the year-to-date /
period-movement arithmetic. BSD7 delegates to it; ``bi_fact_gl_monthly`` is
built from it (D-021), so the two can never disagree.
"""
