import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const scratchBase = path.resolve(dashboard, "../../.local-services");
const guard = path.join(dashboard, "scripts/check_feature_boundaries.mjs");

function fixture() {
  const root = path.join(scratchBase, `feature-boundaries-${process.pid}-${Math.random().toString(36).slice(2)}`);
  const touch = (file, body = "export const x = 1;\n") => {
    mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    writeFileSync(path.join(root, file), body);
  };
  touch("features/fx/routes/exposure.tsx", [
    'import { internal } from "@/features/liquidity/internal";',
    'import { shared } from "@/features/liquidity/public";',
    'import { configuration } from "@/lib/api/client";',
    'import KpiStat from "@/components/ui/KpiStat";',
    'import { strip } from "@/components/bi/InsightStrip";',
    'import { parse } from "@/lib/icaap/parse";',
    'import { useState } from "react";',
    "",
  ].join("\n"));
  touch("features/liquidity/internal.ts");
  touch("features/liquidity/public.ts", 'export const shared = 1;\n');
  touch("features/icaap/parse.ts");
  touch("lib/api/client.ts");
  touch("lib/icaap/parse.ts");
  touch("components/ui/KpiStat.tsx");
  touch("components/bi/InsightStrip.tsx");
  return root;
}

function run(root, args) {
  return spawnSync(process.execPath, [guard, "--root", root, ...args], { encoding: "utf8" });
}

test("flags cross-feature private imports, non-public targets and unowned modules", () => {
  const root = fixture();
  try {
    const result = run(root, []);
    assert.equal(result.status, 1, result.stderr);
    const violations = result.stderr.split("\n").filter((line) => line.startsWith("New feature-boundary violation: "));
    assert.deepEqual(
      violations.map((line) => line.replace("New feature-boundary violation: ", "")).sort(),
      [
        "features/fx/routes/exposure.tsx -> components/bi/InsightStrip",
        "features/fx/routes/exposure.tsx -> features/liquidity/internal",
        "features/fx/routes/exposure.tsx -> lib/icaap/parse",
      ],
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("baseline records violations and only shrinks", () => {
  const root = fixture();
  try {
    const baseline = path.join(root, "feature_boundary_baseline.json");
    assert.equal(run(root, ["--update"]).status, 0);
    assert.deepEqual(JSON.parse(readFileSync(baseline, "utf8")).violations.length, 3);
    assert.equal(run(root, []).status, 0);

    // A violation that no longer occurs must fail until the baseline is
    // regenerated, so the baseline cannot silently grow back.
    const exposure = path.join(root, "features/fx/routes/exposure.tsx");
    writeFileSync(
      exposure,
      ['import { strip } from "@/components/bi/InsightStrip";', 'import { parse } from "@/lib/icaap/parse";', ""].join("\n"),
    );
    const stale = run(root, []);
    assert.equal(stale.status, 1);
    assert.match(stale.stderr, /Stale baseline entry/);
    assert.equal(run(root, ["--update"]).status, 0);
    assert.equal(run(root, []).status, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
