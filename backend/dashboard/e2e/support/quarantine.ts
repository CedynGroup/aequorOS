/**
 * Exact, temporary quarantine for journeys that are known to fail.
 *
 * Empty by default, and CI pins it empty (dashboard-journeys.yml). A journey
 * is quarantined only by naming it here AND declaring it with test.fail —
 * no patterns, no file-wide skips — and only while a linked issue tracks the
 * repair. The custom reporter checks this list against discovery and the
 * test.fail declarations, and Playwright fails the run if an expected failure
 * unexpectedly passes, so a quarantine cannot drift quietly in either
 * direction.
 *
 * Names take the reporter's form: `<file> › <describe> › <title>`.
 */
export const QUARANTINED_JOURNEYS: readonly string[] = [];
