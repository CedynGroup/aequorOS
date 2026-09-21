"""The shared filing review chain: the engine, the bank's template, a return's chain.

``engine`` is the stage engine lifted out of ``services/icaap/workflow.py`` and
is plane-agnostic; ``templates`` and ``chain`` are the BoG package plane's use
of it. The ICAAP calls ``engine`` too — one implementation of "may this officer
decide this stage", not two.
"""
