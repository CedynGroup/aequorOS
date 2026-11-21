/**
 * Exact, temporary quarantine for journeys that are known to fail.
 *
 * A journey is quarantined only by naming it here AND declaring it with
 * test.fail — no patterns, no file-wide skips — with the reason beside the
 * declaration, and only while a linked repair is in flight. Every entry must
 * carry the issue number that tracks the flake (see CODEBASE_CONVENTIONS.md
 * §6); the custom reporter refuses an entry without one, checks this list
 * against discovery and the test.fail declarations, Playwright fails the run if
 * an expected failure unexpectedly passes, and CI enforces quarantine admission
 * separately; see README.md §End-to-end (Playwright) for the required gate
 * changes.
 *
 * Names take the reporter's form: `<file> › <describe> › <title>`.
 */
export type QuarantinedJourney = { readonly name: string; readonly issue: number };

/** Return one complaint per entry that is not a usable quarantine record. */
export function quarantineProblems(
  entries: readonly QuarantinedJourney[],
): readonly string[] {
  return entries.flatMap((entry) => {
    const problems: string[] = [];
    if (typeof entry.name !== "string" || entry.name.trim().length === 0) {
      problems.push("quarantine entry is missing its journey name");
    }
    if (
      typeof entry.issue !== "number" ||
      !Number.isSafeInteger(entry.issue) ||
      entry.issue <= 0
    ) {
      problems.push(
        `quarantine entry ${JSON.stringify(entry.name)} has no positive issue number; ` +
          "every quarantine must name the issue that tracks its flake",
      );
    }
    return problems;
  });
}

export const QUARANTINED_JOURNEYS: readonly QuarantinedJourney[] = [];
