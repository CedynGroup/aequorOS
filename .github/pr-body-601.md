## Summary

Bank-held key revocation and service outages now lock protected fields while numeric features remain usable. The dashboard distinguishes the bank's revocation from a provider outage, offers retry, and restores protected values after recovery. Staff access hides protected records and refuses protected artifact reads without unwrapping keys. This lower branch covers #601; #603 remains a separate stacked review.

## What changed

- Return locked presentation states and clear messages for protected reads and writes, including uploads and attachments.
- Clear cached protected bank data on read or mutation refusals while retaining selected editors and unsaved drafts.
- Keep opaque concentration identities through credit, behavioral, stress, and ICAAP calculations; resolve display labels separately.
- Encrypt BI identity filter values while leaving numeric dashboards, alerts, and subscriptions readable.
- Keep support redaction separate from artifact bytes so archive and signature checks cannot mistake a placeholder for corruption.

## Screenshots

No screenshots were captured in this review phase. The outer validation run owns browser evidence.

## How to review

1. Open a related party or shareholding editor, change a value, revoke the bank key, and submit. Expect the revocation message and the preserved draft.
2. Simulate a provider outage. Expect the outage message, retry, and recovery without losing the draft.
3. Load numeric saved BI content and concentration analytics while the key is unavailable. Expect unchanged numeric results.
4. Inspect protected records and archived artifacts as staff. Expect placeholders or a named locked response, with no key unwrap or false corruption finding.

## Testing

- 188 backend cases passed across the final focused encryption, credit concentration, exposure-book, ICAAP granularity, storage, upload, and attestation runs.
- 18 protected-data/editor UI tests passed, including actual conditional party/shareholding editors, generated-client mutation refusals, BI-client refusals, and recovery.
- 18 PostgreSQL-only cases were skipped because no disposable test database was configured; the PostgreSQL identity-filter fixtures were updated but not executed here.
- Full test, lint, browser, and CI gates remain with the outer executor.

## Risk

Deployment prerequisite: **fresh or reset demo databases only**. This branch intentionally includes no backfill or migration compatibility for existing encrypted installations. Existing records are not re-encrypted. Provider failures remain fail-closed for protected fields, identity-filter configurations, and artifacts.

## Compliance

- Figures changed: a synthetic pair of unrelated 100-unit borrowers retains 50% largest share and HHI 5,000 while locked; the faulty masked-label grouping reported 100% and HHI 10,000.
- Rule basis: no new regulatory rules; this preserves canonical grouping through presentation changes.
- Data, privacy and security: protected bank identities remain encrypted, staff cannot unwrap protected records or artifacts, and known locks clear cached plaintext without discarding form drafts.
- Compliance items: no new readiness scope.

## Follow-ups

#603 ships the separate demo revoke/restore walkthrough. Base #597 exact-match search and the approved fresh-database migration policy are preserved.

<details>

<summary>Validation details</summary>

This is a local PR-description draft for the outer executor; this review phase does not publish or update a PR.

</details>
