"""Market research desk services (AequorOS_Market_Data_and_Curve_Platform.md).

Four seams, mirroring the spec's governance split:

- ``register``  — the methodology register (§5): versioned parameter sets,
  Track-2 propose/approve, approved-version immutability, effective dating.
- ``observations`` — cleaned observations and the manual-entry fallback (§3),
  append-only with current-generation supersession.
- ``determinations`` — Track-1 weekly determinations (§5/§10): observation
  snapshotting with a value-based digest, the maker-checker state machine,
  and append-only corrections via supersession.
- ``publication`` — fan-out of an approved determination into every tenant's
  canonical market-data store through the adapter seam (§2 desk-as-vendor).
"""

from app.services.market_desk import determinations, observations, publication, register

__all__ = ["determinations", "observations", "publication", "register"]
