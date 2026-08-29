import assert from "node:assert/strict";
import {
  hasAccountAdministrationRole,
  ssoApprovalRoleOptions,
} from "./accountAdministration";

assert.equal(hasAccountAdministrationRole(["account_admin"]), true);
assert.equal(hasAccountAdministrationRole(["admin"]), true);
assert.equal(hasAccountAdministrationRole(["approver"]), false);
assert.equal(hasAccountAdministrationRole(["analyst"]), false);

const roleOptions = ssoApprovalRoleOptions({
  AccountAdmin: "account_admin",
  Approver: "approver",
  Analyst: "analyst",
  Viewer: "viewer",
});
assert.deepEqual(roleOptions, [
  "viewer",
  "analyst",
  "approver",
  "account_admin",
]);
assert.equal(roleOptions.includes("admin" as never), false);

console.log("accountAdministration.test.ts: account-plane role checks passed.");
