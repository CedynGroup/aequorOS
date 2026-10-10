import assert from "node:assert/strict";
import test from "node:test";

import { provisionTenant, type ProvisionTenantRequest } from "./api";
import { onboardingKeyState, watchBankKeyRequirement } from "./onboarding";

const emptyKey: NonNullable<ProvisionTenantRequest["encryption_key"]> = {
  provider: "aws_kms",
  key_id: "",
  region: "",
  owner_account: "",
};

function healthResponse(required: boolean) {
  return Response.json({
    service: "operator",
    environment: "test",
    status: "ok",
    bank_key_required: required,
  });
}

async function settle() {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("pending and failed configuration keep keys optional until the API recovers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let required = false;
  let calls = 0;
  const changes: boolean[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    assert.equal(url, "/api/op/operator/health");
    calls++;
    if (calls === 1) throw new TypeError("API unavailable");
    return healthResponse(false);
  });
  t.after(
    watchBankKeyRequirement((value) => {
      required = value;
      changes.push(value);
    }, new EventTarget()),
  );

  assert.equal(onboardingKeyState(required, emptyKey).complete, true);
  await settle();
  assert.deepEqual(changes, []);
  assert.equal(onboardingKeyState(required, emptyKey).encryptionKey, null);
  t.mock.timers.tick(499);
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await settle();
  assert.equal(calls, 2);
  assert.deepEqual(changes, [false]);
  assert.deepEqual(onboardingKeyState(required, emptyKey), {
    required: false,
    complete: true,
    encryptionKey: null,
  });
  t.mock.timers.tick(60000);
  assert.equal(calls, 2);
});

test("a confirmed server requirement blocks an empty key and preserves a complete key", async (t) => {
  let required = false;
  t.mock.method(globalThis, "fetch", async () => healthResponse(true));
  t.after(
    watchBankKeyRequirement((value) => {
      required = value;
    }, new EventTarget()),
  );
  assert.equal(onboardingKeyState(required, emptyKey).complete, true);
  await settle();
  const missing = onboardingKeyState(required, emptyKey);
  assert.equal(missing.required, true);
  assert.equal(missing.complete, false);
  assert.notEqual(missing.encryptionKey, null);

  const key = {
    ...emptyKey,
    key_id: " arn:aws:kms:us-east-1:123456789012:key/fixture ",
    region: " us-east-1 ",
    owner_account: "123456789012",
  };
  for (const requirement of [false, true]) {
    assert.deepEqual(onboardingKeyState(requirement, key), {
      required: true,
      complete: true,
      encryptionKey: {
        ...key,
        key_id: key.key_id.trim(),
        region: key.region.trim(),
      },
    });
  }
});

for (const event of ["online", "focus"]) {
  test(`configuration retries are bounded and resume on ${event}`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const recovery = new EventTarget();
    let required = false;
    let calls = 0;
    let available = false;
    t.mock.method(globalThis, "fetch", async () => {
      calls++;
      if (!available) return new Response("Unavailable", { status: 503 });
      return healthResponse(true);
    });
    t.after(
      watchBankKeyRequirement((value) => {
        required = value;
      }, recovery),
    );
    await settle();
    for (const delay of [500, 1000, 2000, 4000]) {
      t.mock.timers.tick(delay - 1);
      const previousCalls = calls;
      t.mock.timers.tick(1);
      await settle();
      assert.equal(calls, previousCalls + 1);
      assert.equal(onboardingKeyState(required, emptyKey).complete, true);
    }
    assert.equal(calls, 5);
    t.mock.timers.tick(60000);
    await settle();
    assert.equal(calls, 5);

    available = true;
    recovery.dispatchEvent(new Event(event));
    recovery.dispatchEvent(new Event(event));
    await settle();
    assert.equal(calls, 6);
    assert.equal(onboardingKeyState(required, emptyKey).complete, false);
    recovery.dispatchEvent(new Event(event));
    await settle();
    assert.equal(calls, 6);
  });
}

test("cleanup cancels retries and ignores an in-flight health response", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const recovery = new EventTarget();
  const changes: boolean[] = [];
  let finish: (response: Response) => void = () => {};
  const fetch = t.mock.method(
    globalThis,
    "fetch",
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const stop = watchBankKeyRequirement(
    (value) => changes.push(value),
    recovery,
  );
  stop();
  finish(healthResponse(true));
  await settle();
  recovery.dispatchEvent(new Event("online"));
  recovery.dispatchEvent(new Event("focus"));
  assert.equal(changes.length, 0);
  assert.equal(fetch.mock.callCount(), 1);

  fetch.mock.mockImplementation(async () => {
    throw new TypeError("Unavailable");
  });
  const stopRetry = watchBankKeyRequirement(
    (value) => changes.push(value),
    recovery,
  );
  await settle();
  stopRetry();
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(fetch.mock.callCount(), 2);
});

test("optional custody still requires completing any partially entered key", () => {
  for (const partial of [
    { key_id: "arn:aws:kms:us-east-1:123456789012:key/fixture" },
    { region: "us-east-1" },
    { owner_account: "123456789012" },
  ]) {
    const state = onboardingKeyState(false, { ...emptyKey, ...partial });
    assert.equal(state.required, true);
    assert.equal(state.complete, false);
    assert.notEqual(state.encryptionKey, null);
  }
});

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
  let submitted: ProvisionTenantRequest = payload;
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(url, "/api/op/operator/v1/tenants");
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(String(init.body)), submitted);
    return new Response(JSON.stringify(result), {
      headers: { "content-type": "application/json" },
    });
  });
  for (const required of [false, true]) {
    const state = onboardingKeyState(required, payload.encryption_key!);
    assert.equal(state.complete, true);
    submitted = { ...payload, encryption_key: state.encryptionKey };
    assert.deepEqual(await provisionTenant(submitted), result);
  }
  const optional = onboardingKeyState(false, emptyKey);
  assert.equal(optional.complete, true);
  submitted = { ...payload, encryption_key: optional.encryptionKey };
  assert.deepEqual(await provisionTenant(submitted), result);
});
