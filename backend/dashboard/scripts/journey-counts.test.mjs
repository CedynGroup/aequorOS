import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const repo = path.resolve(dashboard, "../..");
const scratch = path.join(repo, ".local-services");
mkdirSync(scratch, { recursive: true });
const verifier = path.join(
  repo,
  ".github/scripts/assert_dashboard_journeys.mjs",
);
function fixture(run) {
  const root = mkdtempSync(path.join(scratch, "journey-counts-"));
  const inheritedSummary = path.join(root, "inherited-summary.md");
  const originalSummary = process.env.GITHUB_STEP_SUMMARY;
  const sentinel = "Existing CI summary\n";
  writeFileSync(inheritedSummary, sentinel);
  process.env.GITHUB_STEP_SUMMARY = inheritedSummary;
  try {
    const result = run(root);
    assert.equal(readFileSync(inheritedSummary, "utf8"), sentinel);
    return result;
  } finally {
    if (originalSummary === undefined) delete process.env.GITHUB_STEP_SUMMARY;
    else process.env.GITHUB_STEP_SUMMARY = originalSummary;
    rmSync(root, { recursive: true, force: true });
  }
}
function reports() {
  return [61, 61, 61, 60].map((count, index) => ({
    shard: { current: index + 1, total: 4 },
    status: "passed",
    discovered: count,
    executed: count,
    skipped: 0,
    skippedNames: [],
    quarantineSize: 0,
    quarantinedExecuted: 0,
    integrityErrors: [],
    journeys: Array.from({ length: count }, (_, n) => ({
      id: `${index}-${n}`,
      name: `bank.spec.ts › shard ${index} journey ${n}`,
      status: "passed",
    })),
  }));
}
function invoke(root, data) {
  const summary = path.join(root, "verifier-summary.md");
  const existingSummary = "Existing fixture summary\n";
  writeFileSync(summary, existingSummary);
  const files = data.map((report, index) => {
    const file = path.join(root, `${index}.json`);
    writeFileSync(file, JSON.stringify(report));
    return file;
  });
  const result = spawnSync(process.execPath, [verifier, ...files], {
    encoding: "utf8",
    env: { ...process.env, GITHUB_STEP_SUMMARY: summary },
  });
  assert.equal(readFileSync(summary, "utf8"), existingSummary + result.stdout);
  return result;
}
test("accepts a complete four-shard run at the 243-journey floor", () =>
  fixture((root) => {
    const result = invoke(root, reports());
    assert.equal(result.status, 0, result.stdout);
    assert.match(result.stdout, /243 executed, 0 skipped/);
  }));
test("rejects a vanished journey even when every shard passed", () =>
  fixture((root) => {
    const data = reports();
    data[0].journeys.pop();
    data[0].discovered--;
    data[0].executed--;
    const result = invoke(root, data);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /floor is 243/);
  }));
test("names skipped journeys and refuses skip-based success", () =>
  fixture((root) => {
    const data = reports();
    const journey = data[0].journeys[0];
    journey.status = "skipped";
    data[0].executed--;
    data[0].skipped = 1;
    data[0].skippedNames = [journey.name];
    const result = invoke(root, data);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /Skipped journeys:\s+- bank.spec.ts/);
  }));
test("does not trust inflated counters", () =>
  fixture((root) => {
    const data = reports();
    data[0].executed = 1000;
    assert.equal(invoke(root, data).status, 1);
  }));
test("rejects duplicate journey results across shards", () =>
  fixture((root) => {
    const data = reports();
    data[1].journeys[0].id = data[0].journeys[0].id;
    assert.equal(invoke(root, data).status, 1);
  }));
test("rejects missing or repeated shard reports", () =>
  fixture((root) => {
    assert.equal(invoke(root, reports().slice(1)).status, 1);
    const data = reports();
    data[1].shard.current = 1;
    assert.equal(invoke(root, data).status, 1);
  }));
test("rejects interrupted results and quarantine integrity errors", () =>
  fixture((root) => {
    const data = reports();
    data[0].journeys[0].status = "interrupted";
    assert.equal(invoke(root, data).status, 1);
    const other = reports();
    other[0].integrityErrors = ["missing test"];
    assert.equal(invoke(root, other).status, 1);
  }));
test("real Playwright reporter writes unique named results, including skipped names", () =>
  fixture((root) => {
    const report = path.join(root, "results.json");
    const reporter = path.join(dashboard, "e2e/support/quarantine-reporter.ts");
    const config = path.join(root, "playwright.config.cjs");
    writeFileSync(
      config,
      `module.exports = ${JSON.stringify({ testDir: root, outputDir: path.join(root, "results"), workers: 1, reporter: [[reporter]], shard: { current: 1, total: 1 } })};`,
    );
    const playwright = path.join(dashboard, "node_modules/@playwright/test");
    writeFileSync(
      path.join(root, "report.spec.cjs"),
      `const {test,expect} = require(${JSON.stringify(playwright)});\nfor(let n=0;n<20;n++)test('journey '+n,()=>expect(1).toBe(1));\ntest.skip('explicit diagnostic',()=>{});`,
    );
    const result = spawnSync(
      process.execPath,
      [path.join(playwright, "cli.js"), "test", "--config", config],
      {
        encoding: "utf8",
        env: { ...process.env, PLAYWRIGHT_QUARANTINE_REPORT: report },
      },
    );
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const data = JSON.parse(readFileSync(report, "utf8"));
    assert.equal(data.discovered, 21);
    assert.equal(data.executed, 20);
    assert.equal(data.skipped, 1);
    assert.equal(new Set(data.journeys.map((journey) => journey.id)).size, 21);
    assert.match(data.skippedNames[0], /explicit diagnostic/);
  }));
