/**
 * Exact, temporary quarantine for pre-existing reporting-anchor fixture drift.
 *
 * The suite first became runnable in CI in the infrastructure change that
 * added this list. Execution revealed that these journeys select regulator
 * anchors for which the canonical E2E book has no exact snapshot. Product
 * behavior is correct to refuse generation rather than substitute an earlier
 * book. Repair is tracked in https://github.com/CedynGroup/aequorOS/issues/151.
 *
 * No patterns or file-wide skips: every expected failure is named. The custom
 * reporter checks this list against discovery and test.fail declarations, and
 * Playwright fails the run if an expected failure unexpectedly passes.
 */
export const QUARANTINED_JOURNEYS = [
  'attestation.spec.ts › attestation surfaces › a generated return shows its attestation state as unsigned',
  'attestation.spec.ts › the certification ceremony › opting in locks submission, and the ceremony enforces what it shows',
  'attestation.spec.ts › the signing workspace › places typed fields from the palette, and refuses an illegible box',
  'attestation.spec.ts › the filed document › an unsigned return offers the base export and claims no signature',
  'full-lifecycle.spec.ts › full lifecycle › journey 5: institution register drives the LRT corporate pack',
  'submission-lifecycle.spec.ts › submission pipeline › journey 1: authenticated returns workspace generates a package',
  'submission-lifecycle.spec.ts › submission pipeline › journey 3: history renders the package/version ledger',
  'submission-lifecycle.spec.ts › submission pipeline › journey 4: a prior version yields its files, its signers, and a diff',
] as const;
