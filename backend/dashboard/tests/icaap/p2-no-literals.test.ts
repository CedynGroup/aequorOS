/**
 * D-024 on the dashboard side: NO REGULATORY NUMBER IN THE P2 COMPONENTS.
 *
 * The founder's rule is that every regulatory or methodological number — a
 * floor, a buffer, a band edge, a shock, a tolerance, a materiality threshold,
 * a deadline — is a control-plane row resolved at runtime, never a literal in
 * engine, service, template or FRONTEND code. The backend half is pinned by
 * `tests/architecture/test_icaap_no_regulatory_literals.py`; this is its
 * counterpart, and it is the reason a preparer can trust that what the ICAAP
 * screens print is what the console says.
 *
 * The rule enforced is deliberately blunt, because a subtle one cannot be
 * trusted: in `components/icaap/p2/`, a numeric literal other than 0 or 1 is a
 * failure, and so is a string literal that is nothing but a number. There is no
 * heuristic about which numbers "look regulatory" — `13` and `56` are
 * indistinguishable to a reader of the source, so neither is allowed to be
 * written by hand.
 *
 * ONE EXEMPTION: `display.ts`, which holds the pixel sizes, row counts, print
 * decimals and character limits, each with a comment saying what it is. That
 * file is reviewed as a whole, and nothing regulatory may enter it.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import ts from "typescript";

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
const SCANNED_DIR = join(ROOT, "components/icaap/p2");

/**
 * P3's review and filing components are held to the same rule.
 *
 * They carry no exemption of their own: every presentation constant they need
 * is imported from `components/icaap/p2/display.ts`, so there is ONE reviewed
 * file of numbers for the ICAAP workspace rather than two.
 */
const P3_DIR = join(ROOT, "components/icaap/p3");

/**
 * The IRRBB standardised framework's own components are held to the same rule.
 *
 * They are not ICAAP components, but the rule they need is identical and the
 * scanner is the same: a shock size, an outlier threshold, a core-deposit cap
 * or a commencement date written into that screen would be exactly the defect
 * D-024 exists to prevent — and those are the numbers a bank is judged on.
 *
 * The directory holds the panels; the entry component sits one level up,
 * beside the other IRRBB workspaces, and is named explicitly so moving it
 * cannot quietly drop it out of the scan.
 */
const SF_DIR = join(ROOT, "components/irr/sf");
const SF_ENTRY = join(ROOT, "components/irr/StandardisedFramework.tsx");

/**
 * The exemptions, and the test files themselves (a test asserting a fixture's
 * shape is not display code).
 *
 * `display.ts` holds the pixel sizes, row counts, print decimals and character
 * limits, each with a comment saying what it is.
 *
 * `availability.tsx` holds HTTP STATUS CODES — 404, 405, 501 — which are
 * structural facts about the transport, not regulatory values, and cannot
 * honestly live in a file about presentation. Its exemption is narrower than
 * `display.ts`'s: a test below asserts that every literal in it is a status
 * code, so the exemption cannot quietly grow into a threshold.
 */
const EXEMPT = new Set(["display.ts", "availability.tsx"]);

/** The inclusive range of an HTTP status code. */
const HTTP_STATUS_MIN = 100;
const HTTP_STATUS_MAX = 599;

/** 0 and 1 are structural — an index, an empty count, a single item. */
const ALLOWED = new Set([0, 1]);

/** A string that is nothing but a number, e.g. "13", "0.5", "1e3". */
const NUMERIC_STRING = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

type Finding = { file: string; line: number; text: string; kind: string };

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.tsx?$/.test(entry)) continue;
    if (entry.endsWith(".test.ts") || entry.endsWith(".test.tsx")) continue;
    if (EXEMPT.has(entry)) continue;
    out.push(full);
  }
  return out;
}

/** Every literal violation in one source text. */
export function scanSource(fileName: string, text: string): Finding[] {
  const source = ts.createSourceFile(
    fileName,
    text,
    ts.ScriptTarget.Latest,
    true,
    /\.tsx$/.test(fileName) ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const findings: Finding[] = [];

  const record = (node: ts.Node, kind: string, literal: string) => {
    const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
    findings.push({ file: fileName, line: line + 1, text: literal, kind });
  };

  const visit = (node: ts.Node): void => {
    if (ts.isNumericLiteral(node)) {
      const value = Number(node.text);
      if (!ALLOWED.has(value)) record(node, "numeric literal", node.text);
    } else if (ts.isStringLiteral(node)) {
      const value = node.text.trim();
      if (NUMERIC_STRING.test(value) && !ALLOWED.has(Number(value))) {
        record(node, "numeric string", node.text);
      }
    } else if (ts.isNoSubstitutionTemplateLiteral(node)) {
      const value = node.text.trim();
      if (NUMERIC_STRING.test(value) && !ALLOWED.has(Number(value))) {
        record(node, "numeric template", node.text);
      }
    }
    ts.forEachChild(node, visit);
  };

  visit(source);
  return findings;
}

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

// ---------------------------------------------------------------------------
// Self-test: a guard that cannot fail proves nothing
// ---------------------------------------------------------------------------

test("the guard catches the shapes it exists to catch", () => {
  const offenders: [string, string][] = [
    ["a hardcoded floor", "const CAR_MIN = 13;"],
    ["a decimal string", 'const floor = "13.5";'],
    ["a shock in a computation", "const shocked = value * 0.85;"],
    ["a band edge in a comparison", "if (score >= 12) { flag(); }"],
    ["a truncation", "const top = rows.slice(0, 20);"],
    ["a size in JSX", "const el = <Icon size={16} />;"],
  ];
  for (const [label, code] of offenders) {
    const found = scanSource("fixture.tsx", code);
    assert.ok(found.length > 0, `the guard missed ${label}: ${code}`);
  }
});

test("the guard accepts 0, 1 and text that merely contains digits", () => {
  const accepted = [
    "const first = rows[0];",
    "const one = 1;",
    "const empty = items.length === 0;",
    'const copy = "Material at a score of 12 or more";',
    'const cls = "mt-0.5 border-l-4 text-navy/80";',
    "const negative = -1;",
  ];
  for (const code of accepted) {
    const found = scanSource("fixture.tsx", code);
    assert.deepEqual(found, [], `the guard wrongly flagged: ${code}`);
  }
});

// `-1` is a unary minus applied to the literal 1, so it is accepted for the
// same reason the backend guard accepts it: it is a sentinel, not a threshold.

// ---------------------------------------------------------------------------
// The real scan
// ---------------------------------------------------------------------------

test("no P2 ICAAP component carries a regulatory number", () => {
  assert.ok(
    existsSync(SCANNED_DIR),
    `the scanned directory is missing: ${SCANNED_DIR}`,
  );
  const files = sourceFiles(SCANNED_DIR);
  assert.ok(
    files.length > 0,
    "the guard found no files to scan — it would pass vacuously",
  );

  const findings: Finding[] = [];
  for (const file of files) {
    findings.push(
      ...scanSource(relative(ROOT, file), readFileSync(file, "utf8")),
    );
  }

  if (findings.length > 0) {
    const report = findings
      .map((f) => `  ${f.file}:${f.line}  ${f.kind}  ${f.text}`)
      .join("\n");
    assert.fail(
      "D-024: a number was written into an ICAAP P2 component.\n" +
        "Every threshold, band, floor, tolerance and deadline must arrive on an " +
        "API payload. A presentation-only number (an icon size, a print decimal, " +
        "a character limit) belongs in components/icaap/p2/display.ts with a " +
        "comment saying what it is.\n" +
        report,
    );
  }

  console.log(
    `D-024 dashboard guard: ${files.length} ICAAP P2 component file(s) clean`,
  );
});

test("no P3 ICAAP component carries a regulatory number", () => {
  assert.ok(
    existsSync(P3_DIR),
    `the scanned directory is missing: ${P3_DIR}`,
  );
  const files = sourceFiles(P3_DIR);
  assert.ok(
    files.length > 0,
    "the guard found no P3 files to scan — it would pass vacuously",
  );

  const findings: Finding[] = [];
  for (const file of files) {
    findings.push(
      ...scanSource(relative(ROOT, file), readFileSync(file, "utf8")),
    );
  }

  if (findings.length > 0) {
    const report = findings
      .map((f) => `  ${f.file}:${f.line}  ${f.kind}  ${f.text}`)
      .join("\n");
    assert.fail(
      "D-024: a number was written into an ICAAP P3 component.\n" +
        "Every threshold, band, floor, tolerance and deadline must arrive on an " +
        "API payload. A presentation-only number (an icon size, a textarea row " +
        "count, a character limit) belongs in components/icaap/p2/display.ts " +
        "with a comment saying what it is.\n" +
        report,
    );
  }

  console.log(
    `D-024 dashboard guard: ${files.length} ICAAP P3 component file(s) clean`,
  );
});

test("no IRRBB standardised framework component carries a regulatory number", () => {
  assert.ok(existsSync(SF_DIR), `the scanned directory is missing: ${SF_DIR}`);
  assert.ok(
    existsSync(SF_ENTRY),
    `the framework entry component is missing: ${SF_ENTRY}`,
  );
  const files = [...sourceFiles(SF_DIR), SF_ENTRY];
  assert.ok(
    files.length > 1,
    "the guard found no framework panels to scan — it would pass vacuously",
  );

  const findings: Finding[] = [];
  for (const file of files) {
    findings.push(
      ...scanSource(relative(ROOT, file), readFileSync(file, "utf8")),
    );
  }

  if (findings.length > 0) {
    const report = findings
      .map((f) => `  ${f.file}:${f.line}  ${f.kind}  ${f.text}`)
      .join("\n");
    assert.fail(
      "D-024: a number was written into an IRRBB standardised framework " +
        "component.\nEvery shock, threshold, cap, band and commencement date " +
        "must arrive on an API payload. A presentation-only number (an icon " +
        "size, a print decimal, a column span) belongs in " +
        "components/irr/sf/display.ts with a comment saying what it is.\n" +
        report,
    );
  }

  console.log(
    `D-024 dashboard guard: ${files.length} IRRBB framework file(s) clean`,
  );
});

test("the exemptions are exactly two filenames, and each dir that uses one has it", () => {
  assert.deepEqual([...EXEMPT].sort(), ["availability.tsx", "display.ts"]);
  for (const file of EXEMPT) {
    assert.ok(
      existsSync(join(SCANNED_DIR, file)),
      `${file} is exempted but does not exist — the exemption is stale`,
    );
  }
  // The framework directory reuses the same two filenames rather than adding
  // exemptions of its own, so the set above stays the whole story.
  for (const file of EXEMPT) {
    assert.ok(
      existsSync(join(SF_DIR, file)),
      `components/irr/sf/${file} is missing — the framework scan is exempting nothing`,
    );
  }
});

/** Every exempted `availability.tsx` in the app, so none escapes the policing. */
const AVAILABILITY_FILES = [
  join(SCANNED_DIR, "availability.tsx"),
  join(SF_DIR, "availability.tsx"),
];

test("every availability exemption holds ONLY HTTP status codes", () => {
  // The narrow exemption, policed. A threshold hidden in one of these files
  // would be just as dangerous as one in a component, so every literal each
  // one carries must be a status code.
  for (const file of AVAILABILITY_FILES) {
    const findings = scanSource(
      relative(ROOT, file),
      readFileSync(file, "utf8"),
    );
    assert.ok(
      findings.length > 0,
      `${relative(ROOT, file)}: the statuses are gone — drop the exemption`,
    );
    for (const finding of findings) {
      const value = Number(finding.text);
      assert.ok(
        Number.isInteger(value) &&
          value >= HTTP_STATUS_MIN &&
          value <= HTTP_STATUS_MAX,
        `${finding.file} carries ${finding.text}, which is not an HTTP status code`,
      );
    }
  }
});

if (failures > 0) {
  console.error(`${failures} D-024 dashboard guard test(s) failed`);
  process.exit(1);
}
