import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

type Contract = {
  id: string;
  name: string;
  status: TestResult["status"] | "not-run";
  expectedStatus: TestCase["expectedStatus"];
};

/** Inventory every collected contract, including missing results and skips. */
export default class ContractReporter implements Reporter {
  private contracts = new Map<string, Contract>();
  private errors: string[] = [];
  private output = "";
  private shard: FullConfig["shard"] = null;

  onBegin(config: FullConfig, suite: Suite): void {
    this.output =
      process.env.PLAYWRIGHT_CONTRACT_REPORT ??
      path.join(
        config.projects[0]?.outputDir ?? "test-results",
        "contracts.json",
      );
    this.shard = config.shard;
    const names = new Set<string>();
    for (const testCase of suite.allTests()) {
      const file = path
        .relative(config.rootDir, testCase.location.file)
        .split(path.sep)
        .join("/");
      const titles = testCase.titlePath().filter(Boolean);
      const fileIndex = titles.findIndex((title) =>
        title.endsWith(path.basename(file)),
      );
      const name = `${file} › ${titles.slice(fileIndex + 1).join(" › ")}`;
      if (names.has(name) || this.contracts.has(testCase.id))
        this.errors.push(`Duplicate contract: ${name}`);
      names.add(name);
      this.contracts.set(testCase.id, {
        id: testCase.id,
        name,
        status: "not-run",
        expectedStatus: testCase.expectedStatus,
      });
    }
  }

  onTestEnd(testCase: TestCase, result: TestResult): void {
    const contract = this.contracts.get(testCase.id);
    if (!contract) {
      this.errors.push(`Uncollected contract result: ${testCase.id}`);
      return;
    }
    // Retries replace the final status rather than increasing the collection.
    contract.status = result.status;
    contract.expectedStatus = testCase.expectedStatus;
  }

  async onEnd(result: FullResult): Promise<{ status: FullResult["status"] }> {
    const contracts = [...this.contracts.values()];
    if (!contracts.length)
      this.errors.push("No browser contracts were collected");
    for (const contract of contracts) {
      if (contract.status !== "passed" || contract.expectedStatus !== "passed")
        this.errors.push(
          `Contract must execute and pass: ${contract.name} (${contract.status}, expected ${contract.expectedStatus})`,
        );
    }
    const skippedNames = contracts
      .filter((contract) => contract.status === "skipped")
      .map((contract) => contract.name);
    const status = this.errors.length ? "failed" : result.status;
    mkdirSync(path.dirname(this.output), { recursive: true });
    writeFileSync(
      this.output,
      JSON.stringify(
        {
          schemaVersion: 1,
          layer: "contract",
          shard: this.shard,
          status,
          discovered: contracts.length,
          executed: contracts.filter(
            (contract) => !["skipped", "not-run"].includes(contract.status),
          ).length,
          skipped: skippedNames.length,
          skippedNames,
          contracts,
          integrityErrors: this.errors,
        },
        null,
        2,
      ) + "\n",
    );
    for (const error of this.errors)
      console.error(`[browser contracts] ${error}`);
    return { status };
  }
}
