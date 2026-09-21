/**
 * The transmit confirmation's claims about each file.
 *
 * This dialog is the last thing an officer reads before a signed return leaves
 * the bank, so a wrong sentence here is worse than no dialog: it tells them
 * they are filing something they are not. The cases below are the ones where a
 * plausible-looking simplification would produce exactly that.
 *
 * Also pinned structurally: the dialog must not restate these rules in its own
 * words. A second phrasing beside this module is how the two drift apart.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  ROLE_COPY,
  filingSetCount,
  signedCell,
  sizeCell,
  type FilingSetEntry,
  type TransmitPreview,
} from "./transmitPreview";

/**
 * The dashboard source root.
 *
 * This file runs compiled, from `.test-out/components/submissions/`, so
 * `__dirname` is the build output — where no `.tsx` source exists. Walk up to
 * the workspace manifest and read the real tree, as `rehearsal.test.ts` does.
 */
function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = join(dir, "..");
  }
  throw new Error("could not locate the dashboard workspace root");
}

const DIALOG_SOURCE = join(
  dashboardRoot(),
  "components",
  "submissions",
  "TransmitConfirmDialog.tsx",
);

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

function entry(over: Partial<FilingSetEntry> = {}): FilingSetEntry {
  return {
    kind: "pdf",
    filename: "LMT_2026-06-30_v1.pdf",
    role: "signed_record",
    sizeBytes: 421_888,
    generatedAtSubmission: false,
    signatureCount: 2,
    ...over,
  };
}

const bytes = (n: number): string => `${n} B`;

test("the formula copy says it is never signed, whatever the count says", () => {
  // It is filed and never signed by construction. A blank or a "0" would read
  // as an oversight an officer should go and fix before filing.
  const cell = signedCell(
    entry({ role: "formula_copy", kind: "xlsx_working", signatureCount: 0 }),
  );
  assert.equal(cell.text, "Never signed");
  assert.equal(cell.tone, "warn");

  // Even if a signature count somehow arrived for it, the rule still holds.
  assert.equal(
    signedCell(entry({ role: "formula_copy", signatureCount: 2 })).text,
    "Never signed",
  );
});

test("the formula copy's description says it recalculates", () => {
  // The whole reason it cannot be the record of truth.
  assert.match(ROLE_COPY.formula_copy.label, /recalculates/i);
});

test("the signed record reports its signatures, with agreeing grammar", () => {
  assert.deepEqual(signedCell(entry({ signatureCount: 2 })), {
    text: "2 signatures",
    tone: "ok",
  });
  assert.deepEqual(signedCell(entry({ signatureCount: 1 })), {
    text: "1 signature",
    tone: "ok",
  });
});

test("no signatures is different from signing not applying", () => {
  // "Unsigned" is a fact about a signable document; "—" means the question
  // does not arise. Collapsing them would hide an uncertified filing.
  assert.equal(signedCell(entry({ signatureCount: 0 })).text, "Unsigned");
  assert.equal(
    signedCell(entry({ role: "data", kind: "csv", signatureCount: null })).text,
    "—",
  );
});

test("a file exported at submission is not shown as absent", () => {
  // `_filing_set` mints a missing filing format as it submits. Showing "—"
  // here would read as "nothing to send" for a file that certainly goes.
  assert.equal(
    sizeCell(entry({ generatedAtSubmission: true, sizeBytes: null }), bytes),
    "Generated now",
  );
  assert.equal(sizeCell(entry({ sizeBytes: 900 }), bytes), "900 B");
  assert.equal(
    sizeCell(entry({ sizeBytes: null, generatedAtSubmission: false }), bytes),
    "—",
  );
});

test("the file count agrees with itself", () => {
  const preview = (n: number): TransmitPreview => ({
    returnCode: "LMT",
    returnName: "Liquidity Monitoring Tools Return",
    reportingDate: "30 Jun 2026",
    institutionName: "Sample Bank Ltd",
    deadline: null,
    channelLabel: "Portal API",
    isSimulated: false,
    institutionCode: null,
    submissionRevision: "1.0",
    isFirstFiling: true,
    filingSet: Array.from({ length: n }, (_unused, i) =>
      entry({ filename: `f${i}.pdf` }),
    ),
    satisfied: [],
    contentDigest: null,
    omissions: [],
  });
  assert.equal(filingSetCount(preview(1)), "1 file");
  assert.equal(filingSetCount(preview(4)), "4 files");
});

test("the dialog defers to this module instead of restating the rules", () => {
  const source = readFileSync(DIALOG_SOURCE, "utf8");
  for (const symbol of ["signedCell", "sizeCell", "ROLE_COPY", "filingSetCount"]) {
    assert.ok(
      source.includes(symbol),
      `the dialog should use ${symbol} rather than its own copy of the rule`,
    );
  }
  assert.ok(
    !/Never signed/.test(source),
    "the 'never signed' sentence belongs to transmitPreview, not the dialog",
  );
});

test("the dialog names no country, regulator or portal literally", () => {
  // Jurisdiction is data. The guard suite scans the backend for this; the
  // dialog is the surface most tempted to hardcode a regulator's name.
  const source = readFileSync(DIALOG_SOURCE, "utf8");
  for (const literal of ["Bank of Ghana", "BoG", "ORASS", "GHS", "Ghana"]) {
    assert.ok(
      !source.includes(literal),
      `${literal} is jurisdiction data — take it from the active jurisdiction`,
    );
  }
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("transmitPreview: all tests passed");
