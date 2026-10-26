import { writeFileSync } from "node:fs";
import type {
  FullConfig,
  FullResult,
  Reporter,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

type Outcome = {
  id: string;
  testId: string;
  name: string;
  status: TestResult["status"];
};

/** Report actual critical-scenario results without adding to journey counts. */
export default class CriticalOutcomeReporter implements Reporter {
  private shard: FullConfig["shard"] = null;
  private results = new Map<string, Outcome[]>();

  constructor(private options: { layer: "journey" | "contract" }) {}

  onBegin(config: FullConfig): void {
    this.shard = config.shard;
  }

  onTestEnd(testCase: TestCase, result: TestResult): void {
    // Retries replace the same test's results; they cannot create extra coverage.
    this.results.set(
      testCase.id,
      testCase.annotations
        .filter((annotation) => annotation.type === "critical-outcome")
        .map((annotation) => ({
          id: annotation.description ?? "",
          testId: testCase.id,
          name: testCase.titlePath().filter(Boolean).join(" › "),
          status: result.status,
        })),
    );
  }

  onEnd(result: FullResult): void {
    const output = process.env.PLAYWRIGHT_CRITICAL_OUTCOMES_REPORT;
    if (!output) return;
    writeFileSync(
      output,
      `${JSON.stringify(
        {
          schemaVersion: 1,
          layer: this.options.layer,
          shard: this.shard,
          status: result.status,
          outcomes: [...this.results.values()].flat(),
        },
        null,
        2,
      )}\n`,
    );
  }
}
