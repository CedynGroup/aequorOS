/**
 * The ICAAP filing copy, policed.
 *
 * Three rules, each with a reason a reviewer can check:
 *
 *  1. **No raw enum reaches a reader.** Every token the API uses has a
 *     translation, and an UNKNOWN token gets a neutral sentence rather than
 *     the token itself. `pending_primary_text` on a board-facing screen is
 *     not a bug a preparer can report usefully.
 *  2. **Nothing unrecognised is drawn as an approval, a pass or a signature.**
 *     An unknown decision, state or status must never carry an affirming
 *     tone, for the same reason the normalisers fail closed.
 *  3. **No digits (D-024) and no jurisdiction literal.** A sentence that
 *     states a floor, a count or a deadline would be a regulatory number
 *     written into display code; `'GHS'`, `'BoG'` and `'Ghana'` belong to
 *     `lib/format.ts`, which reads them from the institution.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  AWAITING_REGULATOR_TEXT_BODY,
  AWAITING_REGULATOR_TEXT_TITLE,
  BOARD_SIGNS_LAST,
  DISCLOSURE_NEVER_PUBLIC,
  FREEZE_IS_FINAL,
  FREEZE_RECORDS_YOU,
  NOTHING_TO_FILE,
  NO_REVIEW_YET,
  REHEARSAL_BODY,
  REHEARSAL_FREEZE_WARNING,
  REHEARSAL_HEADLINE,
  REHEARSAL_SUBMISSION_WARNING,
  attachmentSourceLabel,
  attestationStateCopy,
  blockerTitle,
  chainSourceSentence,
  decisionCopy,
  disclosureStatusCopy,
  gateLabel,
  requirementOriginSentence,
  roleLabel,
  severityCopy,
  slotBlockedSentence,
  stageKindAsk,
  stageKindLabel,
  stageStateCopy,
  templateStatusCopy,
} from "./labels";

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

/** Tones that assert something good has happened. */
const AFFIRMING = new Set(["success", "compliant"]);

const SENTENCES = [
  REHEARSAL_HEADLINE,
  REHEARSAL_BODY,
  REHEARSAL_FREEZE_WARNING,
  REHEARSAL_SUBMISSION_WARNING,
  AWAITING_REGULATOR_TEXT_TITLE,
  AWAITING_REGULATOR_TEXT_BODY,
  BOARD_SIGNS_LAST,
  DISCLOSURE_NEVER_PUBLIC,
  FREEZE_IS_FINAL,
  FREEZE_RECORDS_YOU,
  NOTHING_TO_FILE,
  NO_REVIEW_YET,
];

// ---------------------------------------------------------------------------
// The rehearsal sentence — the single most important copy in this workspace
// ---------------------------------------------------------------------------

test("the rehearsal headline says both that it is practice and that it is never filed", () => {
  assert.match(REHEARSAL_HEADLINE, /practice/i);
  assert.match(REHEARSAL_HEADLINE, /never be filed/i);
});

test("every rehearsal sentence says plainly that nothing is sent or owed", () => {
  assert.match(REHEARSAL_BODY, /not sent to the regulator|nothing here is sent/i);
  assert.match(REHEARSAL_BODY, /satisfies a filing obligation|obligation/i);
  assert.match(REHEARSAL_FREEZE_WARNING, /never be sent/i);
  assert.match(REHEARSAL_SUBMISSION_WARNING, /nothing leaves the platform/i);
});

test("the rehearsal copy tells the reader what to do instead", () => {
  assert.match(REHEARSAL_BODY, /annual cycle/i);
});

// ---------------------------------------------------------------------------
// The exposure-draft reality, stated rather than papered over
// ---------------------------------------------------------------------------

test("the exposure-draft notice says the text is outstanding and what it blocks", () => {
  assert.match(AWAITING_REGULATOR_TEXT_TITLE, /exposure draft/i);
  assert.match(AWAITING_REGULATOR_TEXT_BODY, /has not been published/i);
  assert.match(AWAITING_REGULATOR_TEXT_BODY, /cannot be frozen|rehearsed/i);
});

// ---------------------------------------------------------------------------
// No raw enum, and nothing unrecognised reads as an approval
// ---------------------------------------------------------------------------

test("an unknown decision is neutral, and never says approved", () => {
  for (const token of ["", "escalated", "signed_off", "APPROVED"]) {
    const copy = decisionCopy(token);
    assert.ok(!AFFIRMING.has(copy.tone), `${token} was drawn as an approval`);
    assert.equal(copy.label, "Decision recorded");
    assert.ok(!copy.label.includes(token) || token === "");
  }
  // The ones it does know keep their meaning.
  assert.equal(decisionCopy("approved").label, "Approved");
  assert.equal(decisionCopy("returned").label, "Sent back for changes");
});

test("an unknown signing state is not signed", () => {
  for (const token of ["", "half_signed", "FULLY_CERTIFIED"]) {
    const copy = attestationStateCopy(token);
    assert.ok(!AFFIRMING.has(copy.tone), `${token} read as fully signed`);
  }
  assert.equal(attestationStateCopy("fully_certified").label, "Fully signed");
});

test("no label anywhere is a raw token", () => {
  const rawish = /_|^[a-z]+$/;
  const labels = [
    ...["prepare", "review", "approve", "attest"].map(
      (k) => stageKindLabel(k as never),
    ),
    ...["pending", "current", "done", "returned"].map(
      (k) => stageStateCopy(k as never).label,
    ),
    ...["blocking", "warning", "info"].map((k) => severityCopy(k as never).label),
    ...["preparer", "approver", "board", "made_up"].map(roleLabel),
    ...["freeze", "submission", "optional", "made_up"].map(gateLabel),
    ...["package_upload", "icaap_cycle", "made_up"].map(attachmentSourceLabel),
    ...["draft", "pending_approval", "approved", "published", "rejected", "superseded"].map(
      (k) => templateStatusCopy(k as never).label,
    ),
    ...["draft", "pending_approval", "approved", "published", "rejected", "superseded"].map(
      (k) => disclosureStatusCopy(k as never).label,
    ),
  ];
  for (const label of labels) {
    assert.ok(label.length > 0, "an empty label");
    assert.ok(
      !label.includes("_"),
      `"${label}" is a raw token, not production copy`,
    );
    assert.ok(
      /[A-Z ]/.test(label),
      `"${label}" reads like a token rather than a sentence`,
    );
    assert.ok(!rawish.test(label.replace(/ /g, "")) || /[A-Z]/.test(label));
  }
});

test("every blocker code either has a heading or has none — never a token", () => {
  const known = [
    "attachments_missing",
    "maker_checker",
    "framework_pending_primary_text",
    "rehearsal_not_fileable",
    "review_basis_changed",
  ];
  for (const code of known) {
    const title = blockerTitle(code);
    assert.ok(title !== null, `${code} has no heading`);
    assert.ok(!title!.includes("_"), `${code}'s heading is a token`);
  }
  // An unrecognised code gets NO heading, so the server's sentence stands
  // alone rather than being captioned with a guess.
  assert.equal(blockerTitle("something_nobody_has_seen"), null);
});

test("the pending-primary-text token never reaches a reader as a token", () => {
  const heading = blockerTitle("framework_pending_primary_text");
  assert.ok(heading !== null);
  assert.ok(!heading!.includes("pending_primary_text"));
  assert.match(heading!, /regulator|text/i);
});

// ---------------------------------------------------------------------------
// Signature ordering, and the two sentences that carry the control
// ---------------------------------------------------------------------------

test("a blocked slot explains the ordering instead of reading as a fault", () => {
  const sentence = slotBlockedSentence("approver");
  assert.match(sentence, /signs first/i);
  // It reads as an order, not as a failure.
  assert.ok(!/error|fail|blocked/i.test(sentence));
  // And a role this build has never seen is described generically rather than
  // printed as the token the API sent.
  const unknown = slotBlockedSentence("chief_risk_officer_2");
  assert.ok(
    !unknown.includes("chief_risk_officer_2"),
    "a raw role token reached the reader",
  );
  assert.ok(!unknown.includes("_"), "an underscored token reached the reader");
});

test("the board's ordering sentence says what the order is FOR", () => {
  assert.match(BOARD_SIGNS_LAST, /board signs last/i);
  assert.match(BOARD_SIGNS_LAST, /management approved/i);
});

test("the freeze copy states the cost to the person pressing it", () => {
  assert.match(FREEZE_RECORDS_YOU, /recorded as the officer/i);
  assert.match(FREEZE_RECORDS_YOU, /not the approver|not for the board/i);
  assert.match(FREEZE_IS_FINAL, /nothing in the assessment can be edited/i);
});

test("a document the regime requires says a signing change does not relax it", () => {
  const family = requirementOriginSentence("family");
  assert.match(family, /regime/i);
  assert.match(family, /does not remove this/i);
  const policy = requirementOriginSentence("signing_policy");
  assert.match(policy, /own signing policy/i);
});

test("the disclosure says supervisory information is never published", () => {
  assert.match(DISCLOSURE_NEVER_PUBLIC, /never/i);
  assert.match(DISCLOSURE_NEVER_PUBLIC, /supervis/i);
});

// ---------------------------------------------------------------------------
// D-024 and jurisdiction neutrality, over the whole module
// ---------------------------------------------------------------------------

test("no sentence in this module states a number", () => {
  for (const sentence of SENTENCES) {
    assert.ok(
      !/\d/.test(sentence),
      `a digit reached display copy: "${sentence}"`,
    );
  }
  for (const kind of ["prepare", "review", "approve", "attest"] as const) {
    assert.ok(!/\d/.test(stageKindAsk(kind)));
  }
  assert.ok(!/\d/.test(chainSourceSentence("framework_default")));
});

function dashboardRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 8; index += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const P3_DIR = join(dashboardRoot(), "components/icaap/p3");

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
    out.push(full);
  }
  return out;
}

test("no P3 component writes a jurisdiction literal", () => {
  // The same rule the backend's `test_jurisdiction_neutrality` enforces, on the
  // half that a preparer actually reads. `regShort()`, `centralBankName()`,
  // `currencyCode()` and `fmtCurrency()` read these from the institution's own
  // jurisdiction; a literal here would print cedis on a Nigerian bank's screen.
  const banned = [/\bGHS\b/, /\bBoG\b/, /\bGhana\b/, /\bcedi/i];
  const files = sourceFiles(P3_DIR);
  assert.ok(files.length > 0, "the scan found no files — it would pass vacuously");
  for (const file of files) {
    // Comments are stripped first: the rule is about what a READER sees, and
    // the module's own header has to be able to name the literals it forbids.
    const text = readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/^\s*\/\/.*$/gm, " ");
    for (const pattern of banned) {
      assert.ok(
        !pattern.test(text),
        `${file} carries a jurisdiction literal matching ${pattern}`,
      );
    }
  }
  console.log(
    `ICAAP P3 copy: ${files.length} file(s) free of jurisdiction literals`,
  );
});

if (failures > 0) {
  console.error(`${failures} ICAAP P3 label test(s) failed`);
  process.exit(1);
}
console.log("ICAAP P3 fail-closed labels: all checks passed");
