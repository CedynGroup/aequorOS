/**
 * The collapsed Certification row must never claim a signature that is not there.
 *
 * The case that produced this file: a deployment with e-signing switched off
 * has `canSubmit = true` and zero signatures, and the row read "Fully signed
 * and cleared to be filed" directly above a card reading `UNSIGNED · Not
 * certified`. On a filing surface that is a false statement about who attested
 * to the figures a supervisor will read.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import { certificationSummary, joinRoles } from "./certificationSummary";

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

test("cleared with nothing signed never claims a signature", () => {
  const line = certificationSummary({
    canSubmit: true,
    signatureCount: 0,
    signedRoles: [],
    outstandingLabel: "None",
  });
  assert.ok(!/signed by/i.test(line), `must not claim a signer: ${line}`);
  assert.ok(!/fully signed/i.test(line), `the original defect: ${line}`);
  assert.match(line, /no signature required/i);
  // It must still say it can be filed — the operator needs that answer.
  assert.match(line, /cleared to be filed/i);
});

test("cleared with signatures names who signed", () => {
  assert.equal(
    certificationSummary({
      canSubmit: true,
      signatureCount: 2,
      signedRoles: ["preparer", "approver"],
      outstandingLabel: "None",
    }),
    "Signed by preparer and approver · cleared to be filed.",
  );
});

test("not cleared reports what is outstanding, and claims nothing else", () => {
  const line = certificationSummary({
    canSubmit: false,
    signatureCount: 1,
    signedRoles: ["preparer"],
    outstandingLabel: "1 approver",
  });
  assert.equal(line, "Outstanding: 1 approver.");
  assert.ok(!/cleared/i.test(line), `must not read as cleared: ${line}`);
});

test("clearance is never inferred from the signature count", () => {
  // A fully signed return that the service has NOT cleared is still not
  // cleared. Only `canSubmit` answers that question.
  const line = certificationSummary({
    canSubmit: false,
    signatureCount: 3,
    signedRoles: ["preparer", "approver", "board"],
    outstandingLabel: "1 board",
  });
  assert.ok(!/cleared to be filed/i.test(line), line);
});

test("a signature count with no role labels still avoids inventing one", () => {
  const line = certificationSummary({
    canSubmit: true,
    signatureCount: 2,
    signedRoles: [],
    outstandingLabel: "None",
  });
  assert.equal(line, "2 signed · cleared to be filed.");
  assert.ok(!/signed by/i.test(line), line);
});

test("role lists read as English", () => {
  assert.equal(joinRoles([]), "");
  assert.equal(joinRoles(["preparer"]), "preparer");
  assert.equal(joinRoles(["preparer", "approver"]), "preparer and approver");
  assert.equal(
    joinRoles(["preparer", "approver", "board"]),
    "preparer, approver and board",
  );
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("certificationSummary: all tests passed");
