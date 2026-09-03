import assert from "node:assert/strict";
import { hasAccountAdministrationRole } from "./accountAdministration";

assert.equal(hasAccountAdministrationRole(["account_admin"]), true);
assert.equal(hasAccountAdministrationRole(["admin"]), true);
assert.equal(hasAccountAdministrationRole(["approver"]), false);
assert.equal(hasAccountAdministrationRole(["analyst"]), false);

console.log("accountAdministration.test.ts: account-plane role checks passed.");
