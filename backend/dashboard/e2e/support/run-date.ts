/**
 * The e2e stack's pinned run date. The Playwright config sets `E2E_TODAY`
 * before spawning the bootstrap and the journeys so they share one anchor
 * (H-015, scripts/e2e_bootstrap.py); business dates in request bodies use it
 * instead of the wall clock so a run's inputs are reproducible.
 */
export function e2eRunDate(): string {
  const today = process.env.E2E_TODAY;
  if (!today) {
    throw new Error(
      "E2E_TODAY must be set: the Playwright config pins the shared run date.",
    );
  }
  return today;
}
