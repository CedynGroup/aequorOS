import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repo = fileURLToPath(new URL("../../../", import.meta.url));
const verifier = path.join(
  repo,
  ".github/scripts/assert_dashboard_contracts.mjs",
);
const manifest = JSON.parse(
  readFileSync(
    path.join(repo, ".github/scripts/dashboard_contracts.json"),
    "utf8",
  ),
);
function report() {
  return {
    schemaVersion: 1,
    layer: "contract",
    shard: null,
    status: "passed",
    discovered: manifest.contracts.length,
    executed: manifest.contracts.length,
    skipped: 0,
    skippedNames: [],
    integrityErrors: [],
    contracts: manifest.contracts.map((name, i) => ({
      id: `contract-${i}`,
      name,
      status: "passed",
      expectedStatus: "passed",
    })),
  };
}
function invoke(data, mode = "normal") {
  const root = mkdtempSync(
    path.join(tmpdir(), "dashboard-contract-collection-"),
  );
  try {
    const file = path.join(root, "contracts.json");
    const summary = path.join(root, "summary.md");
    writeFileSync(summary, "Existing summary\n");
    if (mode !== "missing")
      writeFileSync(
        file,
        mode === "corrupt" ? "{bad-json" : JSON.stringify(data),
      );
    const result = spawnSync(
      process.execPath,
      [
        verifier,
        ...(mode === "absent"
          ? []
          : mode === "duplicate"
            ? [file, file]
            : [file]),
      ],
      {
        encoding: "utf8",
        env: { ...process.env, GITHUB_STEP_SUMMARY: summary },
      },
    );
    assert.equal(
      readFileSync(summary, "utf8"),
      "Existing summary\n" + result.stdout,
    );
    return result;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
test("accepts exactly the reviewed collection with every contract executed and passed", () => {
  const result = invoke(report());
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
test("ownership passing cannot hide a skipped or missing noncritical contract", () => {
  for (const mode of ["skipped", "missing"]) {
    const data = report();
    const ownership = data.contracts.find((contract) =>
      contract.name.startsWith("bi-ask.spec.ts"),
    );
    assert.ok(
      ownership,
      "The reviewed ownership contract must remain collected",
    );
    const index = data.contracts.findIndex(
      (contract) => contract !== ownership,
    );
    const ordinary = data.contracts[index];
    if (mode === "skipped") {
      ordinary.status = "skipped";
      data.skipped = 1;
      data.skippedNames = [ordinary.name];
    } else {
      data.contracts.splice(index, 1);
      data.discovered--;
    }
    data.executed--;
    assert.equal(ownership.status, "passed");
    const result = invoke(data);
    assert.equal(result.status, 1);
    assert.ok(result.stdout.includes(ordinary.name), result.stdout);
  }
});
test("refuses unreviewed replacement, duplicate identities, failures, expected failures and malformed counts", () => {
  for (const mutate of [
    (data) => {
      data.contracts[0].name = "replacement.spec.ts › unrelated check";
    },
    (data) => {
      data.contracts[1].id = data.contracts[0].id;
    },
    (data) => {
      data.contracts[1].name = data.contracts[0].name;
    },
    (data) => {
      data.contracts[0].status = "failed";
    },
    (data) => {
      data.contracts[0].expectedStatus = "failed";
    },
    (data) => {
      data.executed--;
    },
    (data) => {
      data.discovered++;
    },
    (data) => {
      data.skippedNames = ["hidden skip"];
    },
    (data) => {
      data.integrityErrors.push("lost result");
    },
    (data) => {
      data.status = "failed";
    },
    (data) => {
      data.schemaVersion = 2;
    },
    (data) => {
      data.shard = { current: 1, total: 2 };
    },
  ]) {
    const data = report();
    mutate(data);
    const result = invoke(data);
    assert.equal(result.status, 1, result.stdout);
  }
});
test("missing, corrupt, absent and duplicated reports fail closed", () => {
  for (const mode of ["missing", "corrupt", "absent", "duplicate"])
    assert.equal(invoke(report(), mode).status, 1);
});
