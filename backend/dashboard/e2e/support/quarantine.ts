/**
 * Exact, temporary quarantine for journeys that are known to fail.
 *
 * A journey is quarantined only by naming it here AND declaring it with
 * test.fail — no patterns, no file-wide skips — with the reason beside the
 * declaration, and only while a linked repair is in flight. The custom
 * reporter checks this list against discovery and the test.fail declarations,
 * Playwright fails the run if an expected failure unexpectedly passes, and
 * CI enforces quarantine admission separately; see README.md §End-to-end
 * (Playwright) for the required gate changes.
 *
 * Names take the reporter's form: `<file> › <describe> › <title>`.
 */
export const QUARANTINED_JOURNEYS: readonly string[] = [];
