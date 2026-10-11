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
const scratch = path.resolve(dashboard, "../../.local-services");
mkdirSync(scratch, { recursive: true });
const runner = path.join(dashboard, "scripts/run-unit-tests.mjs");

function fixture(run) {
  const root = mkdtempSync(path.join(scratch, "unit-collection-"));
  try {
    mkdirSync(path.join(root, "nested"));
    mkdirSync(path.join(root, ".test-out/nested"), { recursive: true });
    writeFileSync(path.join(root, "first.test.ts"), "");
    writeFileSync(path.join(root, "nested/second.test.tsx"), "");
    writeFileSync(
      path.join(root, ".test-out/first.test.js"),
      "console.log('first ran');",
    );
    writeFileSync(
      path.join(root, ".test-out/nested/second.test.js"),
      "console.log('second ran');",
    );
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
function invoke(root, minimum = 2) {
  const report = path.join(root, "report.json");
  const result = spawnSync(
    process.execPath,
    [runner, "--root", root, "--min-files", String(minimum)],
    {
      encoding: "utf8",
      env: { ...process.env, DASHBOARD_UNIT_REPORT: report },
    },
  );
  return { ...result, report: JSON.parse(readFileSync(report, "utf8")) };
}
test("discovers and executes nested TS and TSX tests without a hand-maintained list", () =>
  fixture((root) => {
    const result = invoke(root);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.report.discovered, [
      "first.test.ts",
      "nested/second.test.tsx",
    ]);
    assert.deepEqual(result.report.executed, result.report.discovered);
    assert.match(result.stdout, /first ran/);
    assert.match(result.stdout, /second ran/);
  }));
test("fails before execution if the compiler excluded a source test", () =>
  fixture((root) => {
    rmSync(path.join(root, ".test-out/nested/second.test.js"));
    const result = invoke(root);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /nested\/second.test.tsx/);
    assert.deepEqual(result.report.executed, []);
  }));
test("fails when the collection shrinks below the floor", () =>
  fixture((root) => {
    assert.equal(invoke(root, 3).status, 1);
  }));
test("propagates an assertion failure and names the failing file", () =>
  fixture((root) => {
    writeFileSync(
      path.join(root, ".test-out/first.test.js"),
      "require('node:assert').equal(1, 2);",
    );
    const result = invoke(root);
    assert.equal(result.status, 1);
    assert.deepEqual(result.report.failed, ["first.test.ts"]);
  }));
