## Follow-ups

- Resolve the 91 retained legacy type diagnostics in the nine exact files listed in
  `backend/scripts/type_check_baseline.py::TRANSITIONAL_LEGACY_MODULES`. The live layout
  move preserves their existing per-rule allowances at the new module paths rather
  than mixing typing repairs into the move. Remove each exception as its baseline
  entry is retired; all other `app.live` modules remain strict.
- Replace the pre-existing source-proxy assertions in
  `backend/tests/architecture/test_credit_module.py` and
  `backend/tests/services/test_primary_metric_parity.py` in a separate PR, as agreed.
- Repair the 37 upward live dependencies through composition-owned typed adapter
  and registry wiring in a later architecture change.
