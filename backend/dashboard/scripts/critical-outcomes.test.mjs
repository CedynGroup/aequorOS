import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const repo = path.resolve(dashboard, "../..");
const catalogue = JSON.parse(
  readFileSync(
    path.join(repo, ".github/scripts/dashboard_critical_outcomes.json"),
    "utf8",
  ),
);
const verifier = path.join(
  repo,
  ".github/scripts/assert_dashboard_outcomes.mjs",
);

function fixture(run) {
  const root = mkdtempSync(path.join(tmpdir(), "dashboard-outcomes-"));
  try {
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function reports() {
  const data = Array.from({ length: 5 }, (_, index) => ({
    schemaVersion: 1,
    layer: index < 4 ? "journey" : "contract",
    shard: index < 4 ? { current: index + 1, total: 4 } : null,
    status: "passed",
    outcomes: [],
  }));
  catalogue.outcomes.forEach((outcome, index) => {
    data[outcome.layer === "contract" ? 4 : index % 4].outcomes.push({
      id: outcome.id,
      testId: `test-${index}`,
      name: `A user observes ${outcome.description}`,
      status: "passed",
    });
  });
  return data;
}

function invoke(root, data) {
  const summary = path.join(root, "summary.md");
  const sentinel = "Existing CI summary\n";
  writeFileSync(summary, sentinel);
  const files = data.map((report, index) => {
    const file = path.join(root, `${index}.json`);
    writeFileSync(file, JSON.stringify(report));
    return file;
  });
  const result = spawnSync(process.execPath, [verifier, ...files], {
    encoding: "utf8",
    env: { ...process.env, GITHUB_STEP_SUMMARY: summary },
  });
  assert.equal(readFileSync(summary, "utf8"), sentinel + result.stdout);
  return result;
}

test("accepts the complete catalogue across four journey shards and the contract suite", () =>
  fixture((root) => {
    const result = invoke(root, reports());
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /12 reported, 12 required/);
  }));

test("a passing numeric floor cannot hide an omitted critical scenario", () =>
  fixture((root) => {
    const counts = [54, 53, 53, 53].map((count, index) => ({
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
        name: `Replacement story ${index}-${n}`,
        status: "passed",
      })),
    }));
    const files = counts.map((report, index) => {
      const file = path.join(root, `count-${index}.json`);
      writeFileSync(file, JSON.stringify(report));
      return file;
    });
    const result = spawnSync(
      process.execPath,
      [
        path.join(repo, ".github/scripts/assert_dashboard_journeys.mjs"),
        ...files,
      ],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          GITHUB_STEP_SUMMARY: path.join(root, "counts-summary.md"),
        },
      },
    );
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const data = reports();
    const omitted = data[0].outcomes.pop();
    const critical = invoke(root, data);
    assert.equal(critical.status, 1);
    assert.ok(
      critical.stdout.includes(`Missing critical outcome: ${omitted.id}`),
    );
  }));

test("refuses every nonpassing critical result even when its suite claims success", () =>
  fixture((root) => {
    for (const status of [
      "skipped",
      "failed",
      "timedOut",
      "interrupted",
      "unknown",
    ]) {
      const data = reports();
      data[0].outcomes[0].status = status;
      const result = invoke(root, data);
      assert.equal(result.status, 1);
      assert.ok(result.stdout.includes(`(${status})`));
    }
  }));

test("refuses duplicate, unknown and wrongly layered outcomes", () =>
  fixture((root) => {
    const duplicate = reports();
    duplicate[1].outcomes.push(duplicate[0].outcomes[0]);
    assert.match(
      invoke(root, duplicate).stdout,
      /ERROR: Duplicate critical outcome/,
    );
    const unknown = reports();
    unknown[0].outcomes[0].id = "unreviewed.outcome";
    assert.match(
      invoke(root, unknown).stdout,
      /ERROR: Unknown critical outcome/,
    );
    const wrongLayer = reports();
    wrongLayer[0].outcomes.push(wrongLayer[4].outcomes.pop());
    const result = invoke(root, wrongLayer);
    assert.equal(result.status, 1);
    assert.match(result.stdout, /Wrong layer for question.ownership/);
  }));

test("requires four distinct journey shards and one unsharded contract report", () =>
  fixture((root) => {
    const mutations = [
      (data) => data.pop(),
      (data) => {
        data[1].shard.current = 1;
      },
      (data) => {
        data[0].shard.total = 5;
      },
      (data) => {
        data[4].shard = { current: 1, total: 1 };
      },
      (data) => {
        data[0] = structuredClone(data[4]);
      },
    ];
    for (const mutate of mutations) {
      const data = reports();
      mutate(data);
      assert.equal(invoke(root, data).status, 1);
    }
  }));

test("rejects unsuccessful suites and malformed report protocol fields", () =>
  fixture((root) => {
    const mutations = [
      (data) => {
        data[4].status = "failed";
      },
      (data) => {
        data[0].schemaVersion = 2;
      },
      (data) => {
        data[0].layer = "layout";
      },
      (data) => {
        data[0].outcomes = null;
      },
      (data) => {
        data[0].outcomes[0].testId = "";
      },
      (data) => {
        data[0].outcomes[0].name = "";
      },
      (data) => {
        data[0].outcomes[0].id = "";
      },
    ];
    for (const mutate of mutations) {
      const data = reports();
      mutate(data);
      assert.equal(invoke(root, data).status, 1);
    }
  }));

test("missing and corrupt report files fail with a diagnostic summary", () =>
  fixture((root) => {
    const files = Array.from({ length: 5 }, (_, index) =>
      path.join(root, `${index}.json`),
    );
    for (const corrupt of [false, true]) {
      if (corrupt) writeFileSync(files[0], "{not-json");
      const result = spawnSync(process.execPath, [verifier, ...files], {
        encoding: "utf8",
        env: {
          ...process.env,
          GITHUB_STEP_SUMMARY: path.join(root, "corrupt-summary.md"),
        },
      });
      assert.equal(result.status, 1);
      assert.match(result.stdout, /ERROR:/);
    }
  }));

test("real Playwright reports annotated results, preserves failures and skips, and replaces retry results", () =>
  fixture((root) => {
    const playwright = path.join(dashboard, "node_modules/@playwright/test");
    const config = path.join(root, "playwright.config.cjs");
    const report = path.join(root, "outcomes.json");
    writeFileSync(
      path.join(root, "outcomes.spec.cjs"),
      `
const {test,expect} = require(${JSON.stringify(playwright)});
const critical = id => ({annotation:{type:'critical-outcome',description:id}});
test('ordinary check',()=>expect(1).toBe(1));
test('pinned outcome',()=>{test.info().annotations.push({type:'critical-outcome',description:'passed.id'});expect(2).toBe(2)});
test.skip('skipped outcome',critical('skipped.id'),()=>{});
test('expected failure',critical('failed.id'),()=>{test.fail();expect(1).toBe(2)});
test('retry outcome',({}, info)=>{info.annotations.push({type:'critical-outcome',description:'retry.id'});expect(info.retry).toBe(1)});
`,
    );
    for (const layer of ["journey", "contract"]) {
      writeFileSync(
        config,
        `module.exports = ${JSON.stringify({
          testDir: root,
          outputDir: path.join(root, "results"),
          workers: 1,
          retries: 1,
          shard: layer === "journey" ? { current: 1, total: 4 } : null,
          reporter: [
            [
              path.join(dashboard, "e2e/support/critical-outcome-reporter.ts"),
              { layer },
            ],
          ],
        })};`,
      );
      const result = spawnSync(
        process.execPath,
        [path.join(playwright, "cli.js"), "test", "--config", config],
        {
          encoding: "utf8",
          env: { ...process.env, PLAYWRIGHT_CRITICAL_OUTCOMES_REPORT: report },
        },
      );
      assert.equal(result.status, 0, result.stdout + result.stderr);
      const data = JSON.parse(readFileSync(report, "utf8"));
      assert.equal(data.schemaVersion, 1);
      assert.equal(data.status, "passed");
      assert.equal(data.layer, layer);
      assert.deepEqual(
        data.shard,
        layer === "journey" ? { current: 1, total: 4 } : null,
      );
      assert.equal(data.outcomes.length, 4);
      assert.deepEqual(
        Object.fromEntries(
          data.outcomes.map((outcome) => [outcome.id, outcome.status]),
        ),
        {
          "passed.id": "passed",
          "skipped.id": "skipped",
          "failed.id": "failed",
          "retry.id": "passed",
        },
      );
      assert.equal(
        new Set(data.outcomes.map((outcome) => outcome.testId)).size,
        4,
      );
      assert.ok(data.outcomes.every((outcome) => outcome.name.length > 0));
    }
  }));
