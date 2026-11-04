## Follow-ups

- Resolve the retained legacy typing debt from the live layout migration, following
  the [type-check baseline rules](../CODEBASE_CONVENTIONS.md#type-check-baseline).
- Replace the pre-existing source-proxy assertions in
  `backend/tests/architecture/test_credit_module.py` and
  `backend/tests/services/test_primary_metric_parity.py` in a separate PR, as agreed.
- Repair the 37 upward live dependencies through composition-owned typed adapter
  and registry wiring in a later architecture change.
