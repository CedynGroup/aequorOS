import assert from "node:assert/strict";
import test from "node:test";

import { provisionTenant, type ProvisionTenantRequest } from "./api";

test("onboarding submits the bank key with institution setup", async (t) => {
  const payload: ProvisionTenantRequest = {
    organization_name: "Synthetic organization",
    bank_name: "Synthetic bank",
    license_type: "universal",
    institution_type: "universal_bank",
    jurisdiction_code: "GH",
    currency: "GHS",
    admin_email: "admin@example.test",
    admin_full_name: "Fixture Administrator",
    encryption_key: {
      provider: "aws_kms",
      key_id: "arn:aws:kms:us-east-1:123456789012:key/fixture",
      region: "us-east-1",
      owner_account: "123456789012",
    },
  };
  const result = { succeeded: true, bank_id: "BK-SAMP0001" };
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(url, "/api/op/operator/v1/tenants");
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(String(init.body)), payload);
    return new Response(JSON.stringify(result), {
      headers: { "content-type": "application/json" },
    });
  });
  assert.deepEqual(await provisionTenant(payload), result);
});
