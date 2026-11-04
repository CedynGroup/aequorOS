import { writeFileSync } from "node:fs";
import path from "node:path";
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";
import { QUARANTINED_JOURNEYS } from "./quarantine";

const QUARANTINED_SET = new Set<string>(QUARANTINED_JOURNEYS);

function journeyName(testCase: TestCase): string {
  const fileName = path.basename(testCase.location.file);
  const titlePath = testCase.titlePath().filter(Boolean);
  const fileIndex = titlePath.findIndex((title) => title.endsWith(fileName));
  const journeyPath =
    fileIndex >= 0 ? titlePath.slice(fileIndex + 1) : titlePath;
  return `${fileName} › ${journeyPath.join(" › ")}`;
}

function isExpectedFailure(testCase: TestCase): boolean {
  return (
    testCase.expectedStatus === "failed" ||
    testCase.annotations.some((annotation) => annotation.type === "fail")
  );
}

/** Enforce that the quarantine is exact, applied, and unable to drift quietly. */
export default class QuarantineReporter implements Reporter {
  constructor(
    private readonly options: { requireCompleteExecution?: boolean } = {},
  ) {}

  private errors: string[] = [];
  private discovered = 0;
  private shard: FullConfig["shard"] = null;
  private results = new Map<
    string,
    { id: string; name: string; status: TestResult["status"] }
  >();

  onBegin(_config: FullConfig, suite: Suite): void {
    this.shard = _config.shard;
    const tests = suite.allTests();
    this.discovered = tests.length;
    const discovered = new Map(
      tests.map((testCase) => [journeyName(testCase), testCase]),
    );

    for (const journey of QUARANTINED_SET) {
      const testCase = discovered.get(journey);
      if (!testCase) {
        this.errors.push(
          `quarantine names a journey Playwright did not discover: ${journey}`,
        );
      } else if (!isExpectedFailure(testCase)) {
        this.errors.push(
          `quarantine journey is not declared with test.fail: ${journey}`,
        );
      }
    }

    for (const testCase of tests) {
      const journey = journeyName(testCase);
      if (isExpectedFailure(testCase) && !QUARANTINED_SET.has(journey)) {
        this.errors.push(
          `test.fail journey is missing from the quarantine list: ${journey}`,
        );
      }
    }

    for (const error of this.errors) console.error(`[e2e quarantine] ${error}`);
  }

  onTestEnd(testCase: TestCase, result: TestResult): void {
    // A retry replaces the same journey's result rather than inflating counts.
    this.results.set(testCase.id, {
      id: testCase.id,
      name: journeyName(testCase),
      status: result.status,
    });
  }

  // `async` so the declared return type is the `Promise<...>` arm of Playwright's
  // own union. Returning the bare object compiled against an older Playwright and
  // stopped compiling when the version moved; nothing caught it, because the gated
  // `typecheck` script excludes `e2e/` (a separate tsconfig with its own settings).
  // The script now checks both trees — see `package.json` — so this cannot rot
  // again silently.
  async onEnd(result: FullResult): Promise<{ status?: FullResult["status"] }> {
    const journeys = [...this.results.values()];
    const executed = journeys.filter(
      (journey) => journey.status !== "skipped",
    ).length;
    const skippedNames = journeys
      .filter((journey) => journey.status === "skipped")
      .map((journey) => journey.name);
    const quarantinedExecuted = journeys.filter(
      (journey) =>
        journey.status !== "skipped" && QUARANTINED_SET.has(journey.name),
    ).length;
    if (journeys.length !== this.discovered) {
      this.errors.push(
        `only ${journeys.length} of ${this.discovered} discovered journeys reported a result`,
      );
    }
    const minimumExecuted = this.options.requireCompleteExecution
      ? Math.max(1, this.discovered)
      : 20;
    if (executed < minimumExecuted) {
      this.errors.push(
        `only ${executed} journeys executed; the CI gate expects at least ${minimumExecuted}`,
      );
    }
    if (quarantinedExecuted !== QUARANTINED_JOURNEYS.length) {
      this.errors.push(
        `${quarantinedExecuted} quarantined journeys executed; expected ${QUARANTINED_JOURNEYS.length}`,
      );
    }

    const output = process.env.PLAYWRIGHT_QUARANTINE_REPORT;
    if (output) {
      writeFileSync(
        output,
        `${JSON.stringify(
          {
            shard: this.shard,
            status: result.status,
            discovered: this.discovered,
            executed,
            skipped: skippedNames.length,
            skippedNames,
            journeys,
            quarantinedExecuted,
            quarantineSize: QUARANTINED_JOURNEYS.length,
            integrityErrors: this.errors,
          },
          null,
          2,
        )}\n`,
      );
    }
    for (const error of this.errors) console.error(`[e2e quarantine] ${error}`);
    return this.errors.length > 0
      ? { status: "failed" }
      : { status: result.status };
  }
}
