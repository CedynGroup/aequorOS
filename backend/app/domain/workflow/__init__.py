"""The shared review-chain engine: stage shapes and what a decision round means.

Lifted out of ``domain/icaap/workflow.py`` (P3) so the BoG package plane runs
the same engine rather than a second implementation of it. Everything here is
pure: no session, no models, no HTTP.
"""
