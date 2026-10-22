## Summary

Protect bank-sensitive operational fields with the bank's own key, including saved notification filters. Connected banks retain numeric BI and receive a clear capability explanation for identity analytics. The captain-approved revision replaces transaction-held encryption locks with immutable key versions and bounded session-local key reuse. Implements [#353](https://github.com/CedynGroup/aequorOS/issues/353); secured identity BI remains [#595](https://github.com/CedynGroup/aequorOS/issues/595).

```text
field/object access -> resolve version -> unwrap once per unit -> derive payload key
rotation -> append version -> move current pointer
object publication -> committed version -> ciphertext survives caller rollback
```

## What changed

- Keep exact-match indexes stable across rotation, and migrate mutable fields through a separate resumable re-encryption command.
- Persist version references before object publication so caller rollback cannot orphan an overwritten push manifest.
- Share authenticated key material across field loads and bound storage, disposing of it on transaction completion or session close.
- Keep the design documentation in the separately reviewable design layer; the implementation PR targets that layer.

## Screenshots

No visual changes.

## How to review

- Rotate while ingestion, push, exports, attachments and both downloads run; all must complete without encryption locks.
- Fail a manifest's post-publication stat and roll back; its latest published contents must remain readable.
- Read many encrypted records and objects in one unit; unwrap each required version once, then check revocation at the next boundary.
- Repeat mutable-field re-encryption; completed ciphertext and blind indexes must remain unchanged.

## Testing

- Focused synthetic PostgreSQL/storage checks: 109 passed; final transactional-file rerun after test simplification: 20 passed.
- Changed-file Ruff and whitespace checks passed.
- Covers rotation during every affected product path, both downloads, manifest publication followed by stat failure and rollback, per-version unwrap counts and revocation boundaries, legacy-envelope upgrade, and resumable re-encryption.
- Two AWS SDK footer warnings arose in negative-path tests; no failures. Details: [review evidence](key-version-decision.md).

## Risk

Historical ciphertext, immutable records, stable indexes and backups retain their original key versions. Those KMS keys must remain decrypt-capable; retirement refuses retained references. Revocation takes effect at the next unit-of-work boundary. Existing banks require explicit maintenance initialization after the additive version migration. Schema downgrade refuses once versions exist; rollback requires version-aware application code.

## Compliance

- Figures changed: None.
- Rule basis: Encryption supports BoG CISD §83(7)-(9) and Act 843; no regulatory calculation changes.
- Data, privacy and security: Operational sensitive fields are encrypted; no plaintext key persistence or logging. Approved audit exception: existing audit bytes remain unchanged because no banks are live and historical rows contain synthetic data; new events omit identity names. Approved immutable-history exception: registration and backfill refuse legacy plaintext dashboard versions and package snapshots, retaining all existing guards.
- Compliance items: Bank-held encryption track #353.

## Follow-ups

- #595 restores secured identity BI.
- Retained immutable evidence, objects and index-key transitions require separate approval before old keys can retire.

<details>

<summary>Validation details</summary>

The review executor owns only this fix phase. The outer pipeline owns authoritative tests, lint, stack restacking, publication and CI. No pushes or remote PR writes occurred in this phase.

</details>
