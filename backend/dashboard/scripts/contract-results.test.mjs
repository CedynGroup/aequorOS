import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const playwright = path.join(dashboard, "node_modules/@playwright/test");

test("real Playwright inventories every contract and rejects noncritical skips and expected failures", () => {
  const root = mkdtempSync(path.join(tmpdir(), "dashboard-contract-reporter-"));
  try {
    for (const mode of ["passed", "skipped", "expected-failure", "retry"]) {
      const report = path.join(root, "contracts.json");
      const config = path.join(root, "playwright.config.cjs");
      writeFileSync(
        config,
        `module.exports = ${JSON.stringify({
          testDir: root,
          outputDir: path.join(root, "results"),
          workers: 1,
          retries: mode === "retry" ? 1 : 0,
          reporter: [
            [path.join(dashboard, "e2e/support/contract-reporter.ts")],
          ],
        })};`,
      );
      const ordinary = {
        passed: "test('ordinary export contract',()=>expect(2).toBe(2));",
        skipped: "test.skip('ordinary export contract',()=>{});",
        "expected-failure":
          "test('ordinary export contract',()=>{test.fail();expect(1).toBe(2)});",
        retry:
          "test('ordinary export contract',({},info)=>expect(info.retry).toBe(1));",
      }[mode];
      writeFileSync(
        path.join(root, "contracts.spec.cjs"),
        `
const {test,expect} = require(${JSON.stringify(playwright)});
test('question ownership',()=>{test.info().annotations.push({type:'critical-outcome',description:'question.ownership'});expect(1).toBe(1)});
${ordinary}
`,
      );
      const result = spawnSync(
        process.execPath,
        [path.join(playwright, "cli.js"), "test", "--config", config],
        {
          encoding: "utf8",
          env: { ...process.env, PLAYWRIGHT_CONTRACT_REPORT: report },
        },
      );
      assert.equal(
        result.status,
        ["passed", "retry"].includes(mode) ? 0 : 1,
        result.stdout + result.stderr,
      );
      const data = JSON.parse(readFileSync(report, "utf8"));
      assert.equal(data.schemaVersion, 1);
      assert.equal(data.layer, "contract");
      assert.equal(data.shard, null);
      assert.equal(data.discovered, 2);
      assert.equal(data.contracts.length, 2);
      assert.equal(
        new Set(data.contracts.map((contract) => contract.id)).size,
        2,
      );
      assert.equal(
        data.contracts.find((contract) =>
          contract.name.endsWith("question ownership"),
        ).status,
        "passed",
      );
      const ordinaryResult = data.contracts.find((contract) =>
        contract.name.endsWith("ordinary export contract"),
      );
      assert.equal(
        ordinaryResult.status,
        mode === "skipped"
          ? "skipped"
          : mode === "expected-failure"
            ? "failed"
            : "passed",
      );
      assert.equal(data.executed, mode === "skipped" ? 1 : 2);
      assert.equal(data.skipped, mode === "skipped" ? 1 : 0);
      assert.equal(
        data.integrityErrors.length,
        ["passed", "retry"].includes(mode) ? 0 : 1,
      );
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
