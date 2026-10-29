"""Pure position taxonomy shared by fact derivation and the BI plane.

The vocabulary a canonical position is reduced to before any engine reads it:
the exposure category (and governed risk-weight code) a loan's regulatory class
maps to, and the IRR/FTP product family an exposure category belongs to. No DB,
no I/O — every function is a lookup over a fixed table.
"""
