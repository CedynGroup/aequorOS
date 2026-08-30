import { writeFileSync } from 'node:fs';
import path from 'node:path';
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';
import { QUARANTINED_JOURNEYS } from './quarantine';

const QUARANTINED_SET = new Set<string>(QUARANTINED_JOURNEYS);

function journeyName(testCase: TestCase): string {
  const fileName = path.basename(testCase.location.file);
  const titlePath = testCase.titlePath().filter(Boolean);
  const fileIndex = titlePath.findIndex((title) => title.endsWith(fileName));
  const journeyPath = fileIndex >= 0 ? titlePath.slice(fileIndex + 1) : titlePath;
  return `${fileName} › ${journeyPath.join(' › ')}`;
}

function isExpectedFailure(testCase: TestCase): boolean {
  return (
    testCase.expectedStatus === 'failed' ||
    testCase.annotations.some((annotation) => annotation.type === 'fail')
  );
}

/** Enforce that the quarantine is exact, applied, and unable to drift quietly. */
export default class QuarantineReporter implements Reporter {
  private errors: string[] = [];
  private discovered = 0;
  private executed = 0;
  private skipped = 0;
  private quarantinedExecuted = 0;

  onBegin(_config: FullConfig, suite: Suite): void {
    const tests = suite.allTests();
    this.discovered = tests.length;
    const discovered = new Map(tests.map((testCase) => [journeyName(testCase), testCase]));

    for (const journey of QUARANTINED_SET) {
      const testCase = discovered.get(journey);
      if (!testCase) {
        this.errors.push(`quarantine names a journey Playwright did not discover: ${journey}`);
      } else if (!isExpectedFailure(testCase)) {
        this.errors.push(`quarantine journey is not declared with test.fail: ${journey}`);
      }
    }

    for (const testCase of tests) {
      const journey = journeyName(testCase);
      if (isExpectedFailure(testCase) && !QUARANTINED_SET.has(journey)) {
        this.errors.push(`test.fail journey is missing from the quarantine list: ${journey}`);
      }
    }

    for (const error of this.errors) console.error(`[e2e quarantine] ${error}`);
  }

  onTestEnd(testCase: TestCase, result: TestResult): void {
    if (result.status === 'skipped') {
      this.skipped += 1;
      return;
    }
    this.executed += 1;
    if (QUARANTINED_SET.has(journeyName(testCase))) {
      this.quarantinedExecuted += 1;
    }
  }

  onEnd(result: FullResult): { status?: FullResult['status'] } {
    if (this.executed < 20) {
      this.errors.push(
        `only ${this.executed} journeys executed; the CI gate expects at least 20`
      );
    }
    if (this.quarantinedExecuted !== QUARANTINED_JOURNEYS.length) {
      this.errors.push(
        `${this.quarantinedExecuted} quarantined journeys executed; expected ${QUARANTINED_JOURNEYS.length}`
      );
    }

    const output = process.env.PLAYWRIGHT_QUARANTINE_REPORT;
    if (output) {
      writeFileSync(
        output,
        `${JSON.stringify(
          {
            discovered: this.discovered,
            executed: this.executed,
            skipped: this.skipped,
            quarantinedExecuted: this.quarantinedExecuted,
            quarantineSize: QUARANTINED_JOURNEYS.length,
            integrityErrors: this.errors,
          },
          null,
          2
        )}\n`
      );
    }
    for (const error of this.errors) console.error(`[e2e quarantine] ${error}`);
    return this.errors.length > 0 ? { status: 'failed' } : { status: result.status };
  }
}
