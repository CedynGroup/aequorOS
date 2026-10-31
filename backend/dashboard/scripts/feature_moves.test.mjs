import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const scratchBase = path.resolve(dashboard, "../../.local-services");
const codemod = path.join(dashboard, "scripts/feature_moves.mjs");

function fixture() {
  const root = path.join(scratchBase, `feature-moves-${process.pid}-${Math.random().toString(36).slice(2)}`);
  const touch = (file, body) => {
    mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    writeFileSync(path.join(root, file), Array.isArray(body) ? body.join("\n") : body);
  };
  touch("components/fx/params.ts", ['import { fmtCurrency } from "@/lib/format";', 'import { bars } from "./charts/bars";', "export const splits = bars(fmtCurrency);", ""]);
  touch("components/fx/charts/bars.ts", "export const bars = 1;\n");
  touch("lib/format.ts", "export const fmtCurrency = 1;\n");
  touch("app/lib/format.ts", "export const fmtCurrency = 1;\n");
  // A consumer outside the moved tree, importing through both the alias and a
  // relative specifier that crosses the move boundary.
  touch("app/(app)/fx/page.tsx", ['import { splits } from "@/components/fx/params";', 'import { fmtCurrency } from "../../lib/format";', "export default splits(fmtCurrency);", ""]);
  touch("docs.md", "# FX\n\nSee `components/fx/params` for the split contract.\n");
  spawnSync("git", ["init", "--quiet"], { cwd: root });
  spawnSync("git", ["config", "user.email", "test@example.com"], { cwd: root });
  spawnSync("git", ["config", "user.name", "Test"], { cwd: root });
  spawnSync("git", ["add", "."], { cwd: root });
  spawnSync("git", ["commit", "--quiet", "-m", "fixture"], { cwd: root });
  return root;
}

function run(root, args) {
  return spawnSync(process.execPath, [codemod, "--root", root, ...args], { encoding: "utf8" });
}

test("move relocates a tree, reroutes alias, relative and doc references, and records the ledger", () => {
  const root = fixture();
  try {
    const result = run(root, ["move", "components/fx", "features/fx/components"]);
    assert.equal(result.status, 0, result.stderr);

    assert.equal(existsSync(path.join(root, "components/fx/params.ts")), false);
    const moved = readFileSync(path.join(root, "features/fx/components/params.ts"), "utf8");
    assert.match(moved, /from "@\/lib\/format"/);
    assert.match(moved, /from "\.\/charts\/bars"/);

    const page = readFileSync(path.join(root, "app/(app)/fx/page.tsx"), "utf8");
    assert.match(page, /from "@\/features\/fx\/components\/params"/);
    assert.match(page, /from "\.\.\/\.\.\/lib\/format"/);

    const docs = readFileSync(path.join(root, "docs.md"), "utf8");
    assert.match(docs, /`features\/fx\/components\/params`/);

    const ledger = JSON.parse(readFileSync(path.join(root, "feature_module_moves.json"), "utf8"));
    assert.deepEqual(ledger, [["components/fx", "features/fx/components"]]);
    assert.equal(run(root, ["check"]).status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("check fails when a ledgered module reappears at its old path", () => {
  const root = fixture();
  try {
    assert.equal(run(root, ["move", "components/fx", "features/fx/components"]).status, 0);
    mkdirSync(path.join(root, "components/fx"), { recursive: true });
    writeFileSync(path.join(root, "components/fx/stale.ts"), "export const stale = 1;\n");
    const result = run(root, ["check"]);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /still at its old path/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--shim leaves a route re-export behind and stays out of the ledger", () => {
  const root = fixture();
  try {
    const result = run(root, ["move", "--shim", "app/(app)/fx/page.tsx", "features/fx/routes/exposure.tsx"]);
    assert.equal(result.status, 0, result.stderr);
    const shim = readFileSync(path.join(root, "app/(app)/fx/page.tsx"), "utf8");
    assert.equal(shim, 'export { default } from "@/features/fx/routes/exposure";\n');
    const movedPage = readFileSync(path.join(root, "features/fx/routes/exposure.tsx"), "utf8");
    assert.match(movedPage, /from "@\/components\/fx\/params"/);
    assert.match(movedPage, /from "\.\.\/\.\.\/\.\.\/app\/lib\/format"/);
    const ledger = JSON.parse(readFileSync(path.join(root, "feature_module_moves.json"), "utf8"));
    assert.deepEqual(ledger, []);
    assert.equal(run(root, ["check"]).status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
