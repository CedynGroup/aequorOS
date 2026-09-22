import assert from "node:assert/strict";
import type { EffectiveAuthorityRead } from "@aequoros/risk-service-api";
import {
  accessIndexDestination,
  hasAccountAdministrationAuthority,
  hasAccountDirectoryAuthority,
} from "./accountAdministration";

const authority = (
  permission: "administer" | "view",
  sensitivity: "restricted" | "confidential" = "restricted",
): EffectiveAuthorityRead => ({
  authv: 3,
  organizationCapabilities: [
    {
      module: "account",
      sensitivity,
      permission,
      requiresContextualAuthorization: false,
    },
  ],
  institutionCapabilities: [],
});

assert.equal(hasAccountAdministrationAuthority(authority("administer")), true);
assert.equal(hasAccountAdministrationAuthority(authority("view")), false);
assert.equal(
  hasAccountAdministrationAuthority(authority("administer", "confidential")),
  false,
);
assert.equal(hasAccountAdministrationAuthority(undefined), false);
assert.equal(hasAccountDirectoryAuthority(authority("view")), true);
assert.equal(hasAccountDirectoryAuthority(authority("administer")), false);
assert.equal(
  accessIndexDestination(authority("administer")),
  "/access/members",
);
assert.equal(accessIndexDestination(authority("view")), "/access/my-access");
assert.equal(accessIndexDestination(undefined), "/access/my-access");

console.log(
  "accountAdministration.test.ts: scoped account-plane authority checks passed.",
);
