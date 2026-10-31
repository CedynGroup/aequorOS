import assert from "node:assert/strict";
import type { EffectiveAuthorityRead } from "@aequoros/risk-service-api";
import {
  accessIndexDestination,
  hasAccountAdministrationAuthority,
  hasAccountDirectoryAuthority,
  hasGrantAdministrationAuthority,
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
      dataScope: {
        kind: "all",
        branches: [],
        regions: [],
      },
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
  "/access/my-access",
);
assert.equal(accessIndexDestination(authority("view")), "/access/my-access");
assert.equal(accessIndexDestination(undefined), "/access/my-access");
assert.equal(hasGrantAdministrationAuthority(authority("administer")), false);
assert.equal(hasGrantAdministrationAuthority(undefined), false);
const owner = { ...authority("administer"), canAdministerGrants: true };
assert.equal(hasGrantAdministrationAuthority(owner), true);
assert.equal(accessIndexDestination(owner), "/access/members");

console.log(
  "accountAdministration.test.ts: scoped account-plane authority checks passed.",
);
