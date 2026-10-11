/** Executable contract for writer-only integration-key requests and labels. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  integrationKeyIssueRequest,
  keyAuthorizesNothing,
  purposeLabel,
} from "./integrationKeys";

const BANK = "BK-SAMP0001";

assert.deepEqual(
  integrationKeyIssueRequest({
    bankId: BANK,
    label: "Core banking nightly push",
  }),
  {
    bankId: BANK,
    label: "Core banking nightly push",
    purpose: "writer",
    dataScopeKind: "all",
    dataScopeValues: [],
  },
);
assert.equal(purposeLabel("writer"), "Data push key");
assert.equal(purposeLabel(null), "No purpose recorded");
assert.equal(purposeLabel(undefined), "No purpose recorded");
assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: null }), true);
assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: undefined }), true);
assert.equal(keyAuthorizesNothing({ bankId: null, purpose: "writer" }), true);
assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: "writer" }), false);

// OpenAPI is the published request contract. Every declared field must be
// decided by the builder so a future optional authority dimension cannot fall
// silently through to a server default.
const contract = JSON.parse(
  readFileSync(join(process.cwd(), "..", "openapi-schema.json"), "utf8"),
) as {
  components: {
    schemas: {
      IntegrationKeyIssueRequest: { properties: Record<string, unknown> };
    };
  };
};
const fields = Object.keys(
  contract.components.schemas.IntegrationKeyIssueRequest.properties,
)
  .map((field) =>
    field.replace(/_([a-z])/g, (_segment, letter: string) =>
      letter.toUpperCase(),
    ),
  )
  .sort();
assert.deepEqual(
  Object.keys(integrationKeyIssueRequest({ bankId: BANK, label: "x" })).sort(),
  fields,
);

console.log("integrationKeys.test.ts: all tests passed");
