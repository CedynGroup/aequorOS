## Summary

Filing submission and ICAAP freeze now have executable state-machine coverage
through public services and persisted evidence. The tests retain concrete
regressions for duplicate manual submission and governed ICAAP returns.
An inventory records the remaining workflows for the next staged PRs.

## What changed

- Add bounded, isolated Hypothesis machines for filing/submission and ICAAP freeze,
  including refusals, separation of duties, and cross-bank access.
- Refuse duplicate manual submission without adding another receipt:
  `test_repeated_manual_submission_cannot_add_a_second_receipt`.
- Admit governed returns to the freeze approval stage with a sealed package:
  `test_frozen_version_survives_return_edit_and_refreeze[3]` and
  `test_post_freeze_return_can_reopen_its_freeze_stage`.
- Preserve new-round approvals after post-freeze returns by recording the return
  in the closed round:
  `test_frozen_version_survives_return_edit_and_refreeze[1,2,3]`.
  The compatibility case interprets persisted legacy returns using the frozen
  decision for their package at the shared state-loading boundary, without
  rewriting append-only evidence:
  `test_legacy_post_freeze_return_preserves_evidence_and_allows_refreeze[1,2]`.

## Why

Workflow tests must assert observable transitions and evidence, including retries
and forbidden actions. Upgrades must allow fresh approvals and refreeze when an
older deployment recorded a post-freeze return in the incremented round.

## Screenshots

No UI changes.

## How to review

- Compare the workflow inventory with the public-service action sequences.
- Check that rejected commands preserve state and successful evidence.
- Check legacy return histories, fresh approvals, and repeated refreeze while
  original decision records and sealed snapshots remain unchanged.
- Review the migration's same-stage return constraint and downgrade refusal.

## Testing

### How it was tested

no user-visible change - screenshots not applicable

The focused review check passed all six tests in the ICAAP freeze state-machine
file against native PostgreSQL in a disposable schema (29.00 seconds), including
both persisted legacy-history regressions. Dedicated outer pipeline phases own
the other test, lint, typing, and CI gates.

## Risk

Compatibility interpretation affects ICAAP approval validity. It is scoped to
returns naming a package with a frozen decision in the same tenant/cycle; stored
rounds and evidence stay intact. Legacy returns, current returns, and repeated
freezes exercise the interpretation through public services.

## Compliance

- Figures changed: None.
- Rule basis: No regulatory formula changes.
- Data, privacy and security: Existing append-only decisions and frozen snapshots
  remain unchanged; authorization and separation checks remain active.
- Compliance items: None.

## Follow-ups

Maker-checker/access-request machines and dashboard fast-check properties follow
in separate staged PRs.

<details>

<summary>Validation details</summary>

Focused review command:

```sh
cd backend
AQS_LOCAL_SERVICES=native CASHFLOW_FAST_TEST=1 uv run python scripts/local_services.py run --postgres -- uv run pytest -q --show-capture=no tests/icaap/service/test_freeze_state_machine.py
```

</details>
