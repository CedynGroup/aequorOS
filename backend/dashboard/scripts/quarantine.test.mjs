/**
 * The quarantine contract: a quarantined journey entry must carry an issue.
 *
 * CODEBASE_CONVENTIONS.md §6: quarantine is temporary, issue-backed and never
 * silent. These tests drive the real quarantine reporter through real
 * Playwright with a synthetic quarantine module, so the contract is exercised
 * end to end rather than by mocking the reporter.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, copyFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const repo = path.resolve(dashboard, "../..");
const scratch = path.join(repo, ".local-services");
mkdirSync(scratch, { recursive: true });

function fixture(run) {
  const root = mkdtempSync(path.join(scratch, "quarantine-contract-"));
  try {
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

/**
 * Run the real reporter against a temp quarantine module holding `entries`.
 *
 * The reporter imports "./quarantine", so a copy of it in the temp root picks
 * up the temp list while `quarantineProblems` stays imported from the real
 * module — the contract under test cannot be faked by the fixture.
 */
function runQuarantined(root, entries, tests) {
  const realQuarantine = path.join(dashboard, "e2e/support/quarantine.ts");
  const reexport = path
    .relative(root, realQuarantine)
    .split(path.sep)
    .join("/");
  writeFileSync(
    path.join(root, "quarantine.ts"),
    `export { quarantineProblems } from "./${reexport}";\n` +
      `export const QUARANTINED_JOURNEYS = ${JSON.stringify(entries)};\n`,
  );
  copyFileSync(
    path.join(dashboard, "e2e/support/quarantine-reporter.ts"),
    path.join(root, "quarantine-reporter.ts"),
  );
  const playwright = path.join(dashboard, "node_modules/@playwright/test");
  const config = path.join(root, "playwright.config.cjs");
  writeFileSync(
    config,
    `module.exports = { testDir: ${JSON.stringify(root)}, testMatch: 'report.spec.cjs', ` +
      `outputDir: ${JSON.stringify(path.join(root, "results"))}, workers: 1, ` +
      "reporter: [['./quarantine-reporter.ts', { requireCompleteExecution: true }]] };",
  );
  writeFileSync(
    path.join(root, "report.spec.cjs"),
    `const {test, expect} = require(${JSON.stringify(playwright)});\n${tests}`,
  );
  const report = path.join(root, "results.json");
  const result = spawnSync(
    process.execPath,
    [path.join(playwright, "cli.js"), "test", "--config", config],
    {
      encoding: "utf8",
      env: { ...process.env, CI: "1", PLAYWRIGHT_QUARANTINE_REPORT: report },
    },
  );
  return {
    ...result,
    report: JSON.parse(readFileSync(report, "utf8")),
  };
}

const parkedJourney = "test.fail('parked journey', () => { expect(1).toBe(2); });";

test("a quarantine entry without an issue number fails the run", () =>
  fixture((root) => {
    const result = runQuarantined(
      root,
      [{ name: "report.spec.cjs › parked journey" }],
      parkedJourney,
    );
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.ok(
      result.report.integrityErrors.some((error) =>
        error.includes("no positive issue number"),
      ),
      result.report.integrityErrors,
    );
  }));

test("a quarantine entry with an issue number is accepted and recorded", () =>
  fixture((root) => {
    const entry = { name: "report.spec.cjs › parked journey", issue: 847 };
    const result = runQuarantined(root, [entry], parkedJourney);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.deepEqual(result.report.integrityErrors, []);
    assert.deepEqual(result.report.quarantine, [entry]);
  }));

test("a non-integer issue number is refused like a missing one", () =>
  fixture((root) => {
    const result = runQuarantined(
      root,
      [{ name: "report.spec.cjs › parked journey", issue: 1.5 }],
      parkedJourney,
    );
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.ok(
      result.report.integrityErrors.some((error) =>
        error.includes("no positive issue number"),
      ),
      result.report.integrityErrors,
    );
  }));
