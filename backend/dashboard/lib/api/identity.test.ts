import assert from "node:assert/strict";
import { canHoldSignerIdentity, initialsFrom, roleLabel } from "./identity";

// Mirrors the backend mutation gate (analyst ladder). Account administration
// and read-only roles never hold a signer identity, so the card must not ask.
for (const role of ["admin", "approver", "analyst"]) {
  assert.equal(canHoldSignerIdentity(role), true, role);
}
for (const role of ["account_admin", "examiner", "viewer", "", null, undefined]) {
  assert.equal(canHoldSignerIdentity(role), false, String(role));
}

assert.equal(initialsFrom("Eric Inkoom Danso"), "EI");
assert.equal(initialsFrom("dela@aequoros.com"), "DE");
assert.equal(roleLabel("account_admin"), "Account_admin");

console.log("identity.test.ts: signer eligibility and identity helpers passed.");
