/**
 * A practice run must never be readable as a filing — on EVERY surface that
 * can show a package, not only inside the ICAAP workspace.
 *
 * D-068 deliberately lets a rehearsal cycle do everything a real filing does:
 * freeze, seal a package, take every signature, record a submission. Five
 * independent mechanisms stop one reaching a regulator. None of them stops a
 * person mistaking one for a filing, and on the generic Regulatory Reporting
 * surfaces — History, the approvals queue, the return workspace — a rehearsal
 * arrives carrying the same return code, the same reporting date, the same
 * status pill and the same signature trail. Saying so IS the control.
 *
 * D-080 put `is_rehearsal` on the package read schema; the consumer half was
 * not done, and the security audit found it (S-6): a submitted rehearsal sat
 * in History indistinguishable from a filing. This suite is what makes the
 * omission fail rather than pass quietly the next time a surface is added.
 *
 * Two rules:
 *
 *  1. **The words are one vocabulary.** The generic surfaces use the ICAAP
 *     workspace's own copy (`components/icaap/p3/labels.ts`), never a second
 *     phrasing invented beside it — and never a raw flag name.
 *  2. **Every package surface says so.** Any file that renders a package's
 *     identity must reference the rehearsal marker. The rule is structural on
 *     purpose: a new queue, tab or card gets caught by existing, not by
 *     somebody remembering.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";

// Relative, not aliased: this file is compiled to plain CommonJS and run by
// node, which does not resolve the `@/` path alias.
import {
  REHEARSAL_BODY,
  REHEARSAL_HEADLINE,
  REHEARSAL_SHORT,
} from "../icaap/p3/labels";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const ROOT = dashboardRoot();

// ---------------------------------------------------------------------------
// 1. The words
// ---------------------------------------------------------------------------

test("the short marker is the same sentence, shortened — not a new one", () => {
  assert.match(REHEARSAL_SHORT, /practice run/i);
  assert.match(REHEARSAL_HEADLINE, /practice run/i);
  assert.match(REHEARSAL_HEADLINE, /never be filed/i);
  assert.match(REHEARSAL_BODY, /nothing here satisfies a filing obligation/i);
});

test("no reader is ever shown the flag's own name", () => {
  // `is_rehearsal` / `isRehearsal` / `cycle_kind` are the wire's vocabulary.
  for (const copy of [REHEARSAL_SHORT, REHEARSAL_HEADLINE, REHEARSAL_BODY]) {
    assert.ok(!/is_?[Rr]ehearsal/.test(copy), `raw flag name in: ${copy}`);
    assert.ok(!/cycle_kind|snake_case|_id\b/.test(copy), `raw token in: ${copy}`);
    assert.ok(!/\d/.test(copy), `a digit in display copy (D-024): ${copy}`);
  }
});

// ---------------------------------------------------------------------------
// 2. Every surface that can show a package
// ---------------------------------------------------------------------------

/** Anything that renders a package's identity to a reader. */
const PACKAGE_SIGNALS = [
  "RegulatoryPackageSummaryRead",
  "RegulatoryPackageRead",
  "PackageStatusPill",
];

/** The marker, however the file reads it (package flag or the cycle's kind). */
const REHEARSAL_SIGNALS = ["isRehearsal", "Rehearsal"];

/**
 * Directories a package can surface in. `app/(app)/submissions` is the
 * Regulatory Reporting hub; `components/submissions` its shared parts;
 * `components/icaap/p3` the ICAAP filing workspace, which reaches the same
 * package through its cycle.
 */
const SCANNED = [
  join(ROOT, "app/(app)/submissions"),
  join(ROOT, "components/submissions"),
  join(ROOT, "components/icaap/p3"),
];

/**
 * Surfaces that show a package-shaped row WITHOUT the flag, and why.
 *
 * Empty, and it should stay that way. `/submissions/signatures` was the last
 * entry: `AwaitingSignatureRead` did not carry `is_rehearsal`, so the page had
 * nothing to read. The field was added on 2026-09-20 and the queue now shows
 * the pill — a signer asked to sign a practice run is told which it is BEFORE
 * they sign. Add an entry here only with the reason, never to quiet the scan.
 */
const KNOWN_GAPS: string[] = [];

function sourceFiles(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.tsx?$/.test(entry)) continue;
    if (entry.endsWith(".test.ts") || entry.endsWith(".test.tsx")) continue;
    out.push(full);
  }
  return out;
}

test("every surface that renders a package says when it is a practice run", () => {
  const files = SCANNED.flatMap(sourceFiles);
  assert.ok(files.length > 0, "the scan found no files — it would pass vacuously");
  const carriers: string[] = [];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const stripped = text
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/^\s*\/\/.*$/gm, " ");
    if (!PACKAGE_SIGNALS.some((signal) => stripped.includes(signal))) continue;
    const where = relative(ROOT, file);
    if (KNOWN_GAPS.includes(where)) continue;
    carriers.push(where);
    assert.ok(
      REHEARSAL_SIGNALS.some((signal) => stripped.includes(signal)),
      `${where} renders a package and never says whether it is a practice run`,
    );
  }
  assert.ok(
    carriers.length >= 4,
    `only ${carriers.length} package surface(s) found — the scan has lost its targets`,
  );
  console.log(`Rehearsal labelling: ${carriers.length} package surface(s) covered`);
});

test("the pill and the notice are defined once, where every surface can reach them", () => {
  const shared = readFileSync(join(ROOT, "components/submissions/shared.tsx"), "utf8");
  assert.ok(shared.includes("export function RehearsalPill"));
  assert.ok(shared.includes("export function RehearsalNotice"));
  // Imported, not retyped: one vocabulary.
  assert.match(shared, /from '@\/components\/icaap\/p3\/labels'/);
  assert.ok(
    !/["'`]Practice run["'`]/.test(shared.replace(/\/\*[\s\S]*?\*\//g, " ")),
    "the marker text is written out here instead of imported",
  );
});

if (failures > 0) {
  console.error(`${failures} rehearsal labelling test(s) failed`);
  process.exit(1);
}
console.log("Rehearsal labelling: all checks passed");
