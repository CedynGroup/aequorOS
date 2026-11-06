import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const guard = path.join(dashboard, "scripts/check_feature_boundaries.mjs");

function fixture(t) {
  const root = mkdtempSync(path.join(tmpdir(), "feature-boundaries-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const touch = (file, body = "export const x = 1;\n") => {
    mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    writeFileSync(path.join(root, file), body);
  };
  touch(
    "features/fx/routes/exposure.tsx",
    [
      'import { internal } from "@/features/liquidity/internal";',
      'import { shared } from "@/features/liquidity/public";',
      'import { configuration } from "@/lib/api/client";',
      'import KpiStat from "@/components/ui/KpiStat";',
      'import { strip } from "@/components/bi/InsightStrip";',
      'import { parse } from "@/lib/icaap/parse";',
      'import { useState } from "react";',
      "",
    ].join("\n"),
  );
  touch("features/liquidity/internal.ts");
  touch("features/liquidity/public.ts", "export const shared = 1;\n");
  touch("features/icaap/parse.ts");
  touch("lib/api/client.ts");
  touch("lib/icaap/parse.ts");
  touch("components/ui/KpiStat.tsx");
  touch("components/bi/InsightStrip.tsx");
  return root;
}

function run(root, args) {
  return spawnSync(process.execPath, [guard, "--root", root, ...args], {
    encoding: "utf8",
  });
}

test("flags cross-feature private imports, non-public targets and unowned modules", (t) => {
  const root = fixture(t);
  const result = run(root, []);
  assert.equal(result.status, 1, result.stderr);
  assert.deepEqual(freshPairs(result), [
    "features/fx/routes/exposure.tsx -> components/bi/InsightStrip",
    "features/fx/routes/exposure.tsx -> features/liquidity/internal",
    "features/fx/routes/exposure.tsx -> lib/icaap/parse",
  ]);
});

test("baseline records violations and only shrinks", (t) => {
  const root = fixture(t);
  const baseline = path.join(root, "feature_boundary_baseline.json");
  assert.equal(run(root, ["--update"]).status, 0);
  assert.equal(JSON.parse(readFileSync(baseline, "utf8")).violations.length, 3);
  assert.equal(run(root, []).status, 0);

  // A removed dependency must retire its exact exception before CI passes.
  put(
    root,
    "features/fx/routes/exposure.tsx",
    [
      'import { strip } from "@/components/bi/InsightStrip";',
      'import { parse } from "@/lib/icaap/parse";',
      "",
    ].join("\n"),
  );
  const stale = run(root, []);
  assert.equal(stale.status, 1);
  assert.match(stale.stderr, /Stale baseline entry/);
  assert.equal(run(root, ["--update"]).status, 0);
  assert.equal(run(root, []).status, 0);
});

function put(root, file, body = "export {};\n") {
  mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
  writeFileSync(path.join(root, file), body);
}

function baselinedFixture(t) {
  const root = fixture(t);
  const initial = run(root, ["--update"]);
  assert.equal(initial.status, 0, initial.stderr);
  return root;
}

function freshPairs(result) {
  return result.stderr
    .split("\n")
    .filter((line) => line.startsWith("New feature-boundary violation: "))
    .map((line) => line.replace("New feature-boundary violation: ", ""));
}

test("the actual dashboard passes the expanded boundary scan", () => {
  const result = run(dashboard, []);
  assert.equal(result.status, 0, result.stderr);
  assert.match(
    result.stdout,
    /Feature-boundary check passed: [1-9]\d* recorded/,
  );
});

for (const origin of [
  "components/charts/Probe.tsx",
  "components/ui/Probe.tsx",
  "components/shell/Probe.tsx",
  "lib/api/probe.ts",
  "lib/helper.ts",
  "lib/nested/probe.ts",
]) {
  for (const target of ["internal", "public"]) {
    test(`shared origin ${origin} cannot depend on feature ${target}`, (t) => {
      const root = baselinedFixture(t);
      put(root, origin, `import "@/features/liquidity/${target}";`);
      const result = run(root, []);
      assert.equal(result.status, 1, result.stdout);
      assert.deepEqual(freshPairs(result), [
        `${origin} -> features/liquidity/${target}`,
      ]);
    });
  }
}

for (const [name, source] of [
  ["relative", 'import "../../features/liquidity/internal";'],
  ["normalized alias", 'import "@/features/fx/../liquidity/internal";'],
  ["explicit extension", 'import "@/features/liquidity/internal.ts";'],
  [
    "multiline import",
    'import {\n x,\n} from\n "@/features/liquidity/internal";',
  ],
  ["re-export", 'export { x } from "@/features/liquidity/internal";'],
  ["wildcard re-export", 'export * from "@/features/liquidity/internal";'],
  [
    "namespace re-export",
    'export * as x from "@/features/liquidity/internal";',
  ],
  [
    "type-only import",
    'import type { X } from "@/features/liquidity/internal";',
  ],
  [
    "type-only export",
    'export type { X } from "@/features/liquidity/internal";',
  ],
  [
    "import type expression",
    'type X = import("@/features/liquidity/internal").X;',
  ],
  [
    "dynamic import",
    'const load = () => import(\n "@/features/liquidity/internal"\n);',
  ],
  [
    "template dynamic literal",
    "const load = () => import(`@/features/liquidity/internal`);",
  ],
  ["require", 'const x = require(\n "@/features/liquidity/internal"\n);'],
  [
    "template require literal",
    "const x = require(`@/features/liquidity/internal`);",
  ],
  ["import equals", 'import x = require("@/features/liquidity/internal");'],
]) {
  test(`shared backedges are detected through ${name}`, (t) => {
    const root = baselinedFixture(t);
    put(root, "components/charts/Probe.tsx", source);
    const result = run(root, []);
    assert.equal(result.status, 1, result.stdout);
    assert.deepEqual(freshPairs(result), [
      "components/charts/Probe.tsx -> features/liquidity/internal",
    ]);
  });
}

for (const [name, source] of [
  ["alias", 'import "@/app/composition";'],
  ["relative", 'import "../../../app/composition";'],
  ["type-only", 'import type { X } from "@/app/composition";'],
  ["re-export", 'export * from "@/app/composition";'],
  ["dynamic", 'const load = () => import("@/app/composition");'],
  ["require", 'const x = require("@/app/composition");'],
]) {
  test(`feature-to-app ${name} dependencies are forbidden`, (t) => {
    const root = baselinedFixture(t);
    put(root, "app/composition.ts");
    put(root, "features/fx/routes/probe.tsx", source);
    const result = run(root, []);
    assert.equal(result.status, 1, result.stdout);
    assert.deepEqual(freshPairs(result), [
      "features/fx/routes/probe.tsx -> app/composition",
    ]);
  });
}

test("own internals, cross-feature public interfaces, shared code and app composition remain allowed", (t) => {
  const root = fixture(t);
  put(root, "features/fx/internal.ts");
  put(
    root,
    "features/fx/routes/exposure.tsx",
    [
      'import "@/features/fx/internal";',
      'import "@/features/liquidity/public";',
      'export type * from "../../liquidity/public";',
      'import "@/lib/api/client";',
      'import "@/components/ui/KpiStat";',
      'import "react";',
      'import "./styles.css";',
    ].join("\n"),
  );
  put(root, "app/page.tsx", 'export * from "@/features/fx/internal";');
  put(root, "lib/helper.ts", 'import "@/components/ui/KpiStat";');
  put(root, "components/charts/Probe.tsx", 'import "@/lib/helper";');
  const result = run(root, []);
  assert.equal(result.status, 0, result.stderr);
});

test("comments, strings, templates, JSX prose and nonliteral runtime imports do not invent edges", (t) => {
  const root = fixture(t);
  const prose = [
    '// import "@/features/liquidity/internal";',
    '/* export * from "@/features/liquidity/internal";',
    'import("@/features/liquidity/internal"); */',
    'const help = `import { x } from "@/features/liquidity/internal";`;',
    "const quoted = 'require(\"@/features/liquidity/internal\")';",
    'const view = <div>import("@/features/liquidity/internal")</div>;',
    "const load = (name: string) => import(name);",
    "const common = (name: string) => require(`./${name}`);",
  ].join("\n");
  put(root, "features/fx/routes/exposure.tsx", prose);
  put(root, "components/charts/Prose.tsx", prose);
  put(
    root,
    "lib/api/not-executed.ts",
    'throw new Error("must not execute source");',
  );
  const result = run(root, []);
  assert.equal(result.status, 0, result.stderr);
});

test("new feature roots and index targets are discovered; duplicate syntax counts one exact pair", (t) => {
  const root = baselinedFixture(t);
  put(root, "features/new_feature/internal/index.ts");
  put(
    root,
    "lib/api/probe.ts",
    [
      'import "@/features/new_feature/internal";',
      'export * from "../../features/new_feature/internal/index";',
      'const load = () => import("@/features/new_feature/internal/index.ts");',
    ].join("\n"),
  );
  const result = run(root, []);
  assert.equal(result.status, 1, result.stdout);
  assert.deepEqual(freshPairs(result), [
    "lib/api/probe.ts -> features/new_feature/internal/index",
  ]);
});

test("a feature public interface does not exempt nested private targets", (t) => {
  const root = baselinedFixture(t);
  put(root, "features/liquidity/public/private.ts");
  put(
    root,
    "features/fx/routes/probe.tsx",
    'export * from "@/features/liquidity/public/private";',
  );
  const result = run(root, []);
  assert.equal(result.status, 1, result.stdout);
  assert.deepEqual(freshPairs(result), [
    "features/fx/routes/probe.tsx -> features/liquidity/public/private",
  ]);
});

test("grandfathered shared pairs become stale and cannot be swapped for new debt of the same size", (t) => {
  const root = baselinedFixture(t);
  put(
    root,
    "components/charts/Probe.tsx",
    'import "@/features/liquidity/internal";',
  );
  assert.equal(run(root, ["--update"]).status, 0);
  assert.equal(run(root, []).status, 0);
  put(
    root,
    "components/charts/Probe.tsx",
    'import "@/features/liquidity/public";',
  );
  const result = run(root, []);
  assert.equal(result.status, 1, result.stdout);
  assert.deepEqual(freshPairs(result), [
    "components/charts/Probe.tsx -> features/liquidity/public",
  ]);
  assert.match(
    result.stderr,
    /Stale baseline entry \(run --update\): components\/charts\/Probe.tsx -> features\/liquidity\/internal/,
  );
});

for (const directory of ["features", "components", "lib"]) {
  for (const state of ["missing", "empty"]) {
    test(`fails closed on ${state} ${directory} origin population`, (t) => {
      const root = fixture(t);
      rmSync(path.join(root, directory), { recursive: true });
      if (state === "empty") mkdirSync(path.join(root, directory));
      const result = run(root, ["--update"]);
      assert.equal(result.status, 1, result.stdout);
      assert.match(result.stderr, /ENOENT|No governed source files/);
    });
  }
}

test("root files cannot replace the discovered feature-root population", (t) => {
  const root = fixture(t);
  rmSync(path.join(root, "features"), { recursive: true });
  put(root, "features/orphan.ts");
  const result = run(root, []);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /no feature roots/);
});

test("legacy-only components cannot replace the shared-origin population", (t) => {
  const root = fixture(t);
  rmSync(path.join(root, "components/ui"), { recursive: true });
  const result = run(root, []);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /No governed source files under components/);
});

test("uninspectable source fails instead of silently reducing coverage", (t) => {
  const root = fixture(t);
  symlinkSync("missing.ts", path.join(root, "lib/api/broken.ts"));
  const result = run(root, ["--update"]);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /ENOENT/);
});

for (const file of [
  "features/fx/routes/exposure.tsx",
  "components/ui/Probe.tsx",
  "lib/api/probe.ts",
]) {
  test(`parser errors in ${file} cannot pass through recovery or --update`, (t) => {
    const root = baselinedFixture(t);
    const baseline = path.join(root, "feature_boundary_baseline.json");
    const before = readFileSync(baseline, "utf8");
    put(root, file, 'import { x from "@/features/liquidity/internal";');
    const result = run(root, ["--update"]);
    assert.equal(result.status, 1, result.stdout);
    assert.match(result.stderr, /invalid syntax/);
    assert.ok(result.stderr.includes(file), result.stderr);
    assert.equal(readFileSync(baseline, "utf8"), before);
  });
}

for (const body of [
  "{",
  "{}",
  '{"violations": [null]}',
  '{"violations": [1]}',
  '{"violations": ["not an exact pair"]}',
  '{"violations": ["a.ts -> b", "a.ts -> b"]}',
]) {
  test(`rejects malformed baseline ${body}`, (t) => {
    const root = fixture(t);
    put(root, "feature_boundary_baseline.json", body);
    const result = run(root, []);
    assert.equal(result.status, 1, result.stdout);
    assert.match(result.stderr, /Malformed baseline|JSON/);
  });
}
