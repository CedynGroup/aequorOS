/**
 * Exact, temporary quarantine for journeys that are known to fail.
 *
 * A journey is quarantined only by naming it here AND declaring it with
 * test.fail — no patterns, no file-wide skips — with the reason beside the
 * declaration, and only while a linked repair is in flight. The custom
 * reporter checks this list against discovery and the test.fail declarations,
 * Playwright fails the run if an expected failure unexpectedly passes, and
 * dashboard-journeys.yml pins the list's size, so a quarantine cannot drift
 * quietly in either direction and parking a journey is always a visible,
 * reviewed decision.
 *
 * Names take the reporter's form: `<file> › <describe> › <title>`.
 */
export const QUARANTINED_JOURNEYS: readonly string[] = [
  // SSO step-up return leg crosses the cookie jar (request.nextUrl.origin is
  // localhost under Next dev); the requestOrigin fix for the step-up routes is
  // on fm/aeq-sso-local-issuer-e2e-journey, stacked on PR #205.
  "attestation.spec.ts › the certification ceremony › opting in locks submission, and the ceremony enforces what it shows",
  // Event-driven LRT packs have no reporting anchors by design, and the
  // workspace offers no other way to choose their as-of date — a product gap.
  "full-lifecycle.spec.ts › full lifecycle › journey 5: institution register drives the LRT corporate pack",
];
