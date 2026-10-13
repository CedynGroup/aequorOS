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
const guard = path.join(dashboard, "scripts/check-unit-coverage.mjs");
function json(root, file, value) {
  writeFileSync(path.join(root, file), JSON.stringify(value));
}
function report(root, file, counts) {
  const location = path.join(root, file);
  return {
    path: location,
    statementMap: Object.fromEntries(
      counts.map((_, id) => [
        id,
        {
          start: { line: id + 1, column: 0 },
          end: { line: id + 1, column: 10 },
        },
      ]),
    ),
    s: Object.fromEntries(counts.map((count, id) => [id, count])),
    fnMap: {},
    f: {},
    branchMap: {},
    b: {},
  };
}
function fixture(run) {
  const root = mkdtempSync(path.join(scratch, "coverage-guard-"));
  try {
    for (const directory of [
      "app",
      "components",
      "lib",
      "coverage/vitest",
      "coverage/legacy",
    ])
      mkdirSync(path.join(root, directory), { recursive: true });
    writeFileSync(
      path.join(root, "lib/feature.ts"),
      "export const one = 1;\nexport const two = 2;\nexport const three = 3;\n",
    );
    const discovered = Array.from(
      { length: 55 },
      (_, id) => `case-${id}.test.ts`,
    );
    json(root, "coverage/legacy-tests.json", {
      discovered,
      executed: discovered,
      failed: [],
    });
    json(root, "coverage/vitest-tests.json", {
      numTotalTests: 8,
      numPassedTests: 8,
      numFailedTests: 0,
      numPendingTests: 0,
      numTodoTests: 0,
      success: true,
    });
    json(root, "coverage/vitest/coverage-final.json", {
      [path.join(root, "lib/feature.ts")]: report(
        root,
        "lib/feature.ts",
        [0, 0, 0],
      ),
    });
    // c8's line mapping can contain extra lines; they must not dilute Vitest's AST denominator.
    json(root, "coverage/legacy/coverage-final.json", {
      [path.join(root, "lib/feature.ts")]: report(
        root,
        "lib/feature.ts",
        [1, 1, 0, 1],
      ),
    });
    json(root, "unit-coverage-baseline.json", {
      version: 1,
      maxUncovered: 1,
      files: { "lib/feature.ts": 1 },
    });
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
function invoke(root, ...args) {
  return spawnSync(process.execPath, [guard, "--root", root, ...args], {
    encoding: "utf8",
  });
}
function load(root, file) {
  return JSON.parse(readFileSync(path.join(root, file), "utf8"));
}

test("combines child-process and native hits on the canonical source lines", () =>
  fixture((root) => {
    const native = load(root, "coverage/vitest/coverage-final.json");
    Object.values(native)[0].s[2] = 1;
    json(root, "coverage/vitest/coverage-final.json", native);
    const result = invoke(root);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(load(root, "coverage/combined/summary.json").total, {
      covered: 3,
      uncovered: 0,
      lines: 3,
      percent: 100,
    });
    assert.match(
      readFileSync(path.join(root, "coverage/combined/lcov.info"), "utf8"),
      /LF:3\nLH:3/,
    );
  }));
test("reports the complete denominator and coverage from legacy execution", () =>
  fixture((root) => {
    assert.equal(invoke(root).status, 0);
    assert.deepEqual(load(root, "coverage/combined/summary.json").total, {
      covered: 2,
      uncovered: 1,
      lines: 3,
      percent: 66.67,
    });
  }));
test("rejects a production file omitted from native coverage", () =>
  fixture((root) => {
    writeFileSync(
      path.join(root, "components/new.tsx"),
      "export const New = () => null;",
    );
    const result = invoke(root);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /every production/);
  }));
test("rejects missing engine reports", () =>
  fixture((root) => {
    rmSync(path.join(root, "coverage/legacy/coverage-final.json"));
    assert.equal(invoke(root).status, 1);
  }));
test("rejects skipped native tests and incomplete legacy collection", () =>
  fixture((root) => {
    const native = load(root, "coverage/vitest-tests.json");
    native.numPendingTests = 1;
    json(root, "coverage/vitest-tests.json", native);
    assert.equal(invoke(root).status, 1);
    native.numPendingTests = 0;
    json(root, "coverage/vitest-tests.json", native);
    const legacy = load(root, "coverage/legacy-tests.json");
    legacy.executed.pop();
    json(root, "coverage/legacy-tests.json", legacy);
    assert.equal(invoke(root).status, 1);
  }));
test("rejects invalid hit counters", () =>
  fixture((root) => {
    const legacy = load(root, "coverage/legacy/coverage-final.json");
    Object.values(legacy)[0].s[0] = -1;
    json(root, "coverage/legacy/coverage-final.json", legacy);
    const result = invoke(root);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Invalid coverage counter/);
  }));
test("refuses coverage regression and cannot raise the baseline through update", () =>
  fixture((root) => {
    const legacy = load(root, "coverage/legacy/coverage-final.json");
    Object.values(legacy)[0].s[0] = 0;
    json(root, "coverage/legacy/coverage-final.json", legacy);
    const before = readFileSync(
      path.join(root, "unit-coverage-baseline.json"),
      "utf8",
    );
    const result = invoke(root, "--update");
    assert.equal(result.status, 1);
    assert.match(result.stderr, /may only shrink/);
    assert.equal(
      readFileSync(path.join(root, "unit-coverage-baseline.json"), "utf8"),
      before,
    );
  }));
test("tightens the baseline when uncovered lines shrink", () =>
  fixture((root) => {
    const native = load(root, "coverage/vitest/coverage-final.json");
    Object.values(native)[0].s[2] = 1;
    json(root, "coverage/vitest/coverage-final.json", native);
    const result = invoke(root, "--update");
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(load(root, "unit-coverage-baseline.json"), {
      version: 1,
      maxUncovered: 0,
      files: { "lib/feature.ts": 0 },
    });
  }));
test("requires a baseline in ordinary CI mode", () =>
  fixture((root) => {
    rmSync(path.join(root, "unit-coverage-baseline.json"));
    assert.equal(invoke(root).status, 1);
    assert.equal(invoke(root, "--update").status, 0);
  }));
test("per-file ceilings stop another file's improvement hiding a regression", () =>
  fixture((root) => {
    writeFileSync(path.join(root, "lib/other.ts"), "export const other = 1;");
    const native = load(root, "coverage/vitest/coverage-final.json");
    native[path.join(root, "lib/other.ts")] = report(root, "lib/other.ts", [1]);
    json(root, "coverage/vitest/coverage-final.json", native);
    json(root, "unit-coverage-baseline.json", {
      version: 1,
      maxUncovered: 1,
      files: { "lib/feature.ts": 0, "lib/other.ts": 1 },
    });
    assert.equal(invoke(root).status, 1);
  }));
