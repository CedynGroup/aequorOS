/**
 * The integration-key request states its purpose, and states everything else.
 *
 * Audit A360-2 H4: the dashboard posted `{bank_id, label}` and nothing else, so
 * the server defaulted every key to `writer`, whose authority is `{ingest}`; the
 * Power BI feed asks for `view`; the two are disjoint, so every feed pull was
 * refused and the bank's guide pointed at a choice the application did not
 * offer. Nothing covered what the dashboard actually sent. This does.
 *
 * Two halves. The pure half executes the builder. The parity half reads the
 * GENERATED serializer's source and asserts that every field it emits is one the
 * builder states — the permanent form of the stale-client tripwire, in the shape
 * `grants.test.ts` established: `IntegrationKeyIssueRequestToJSONTyped` returns
 * a hand-enumerated literal with no spread, so a field the builder leaves unset
 * is decided by the server's column default, silently, with a 201.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  ANALYTICS_FEED_PURPOSE,
  DATA_PUSH_PURPOSE,
  INTEGRATION_KEY_PURPOSES,
  integrationKeyIssueRequest,
  keyAuthorizesNothing,
  purposeLabel,
  WHOLE_INSTITUTION_SCOPE,
} from "./integrationKeys";

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

function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const BANK = "BK-SAMP0001";

// ---------------------------------------------------------------------------
// The builder
// ---------------------------------------------------------------------------

test("an analytics feed key is posted as a reader, never as the server default", () => {
  const request = integrationKeyIssueRequest({
    bankId: BANK,
    label: "Power BI gateway",
    purpose: ANALYTICS_FEED_PURPOSE,
  });
  assert.equal(request.purpose, "reader");
  assert.equal(request.bankId, BANK);
  assert.equal(request.label, "Power BI gateway");
});

test("a data push key is posted as a writer, explicitly", () => {
  const request = integrationKeyIssueRequest({
    bankId: BANK,
    label: "Core banking nightly push",
    purpose: DATA_PUSH_PURPOSE,
  });
  assert.equal(request.purpose, "writer");
});

test("the scope is stated as the whole institution rather than left to the server", () => {
  const request = integrationKeyIssueRequest({
    bankId: BANK,
    label: "x",
    purpose: ANALYTICS_FEED_PURPOSE,
  });
  assert.equal(request.dataScopeKind, WHOLE_INSTITUTION_SCOPE);
  assert.equal(request.dataScopeKind, "all");
  assert.deepEqual(request.dataScopeValues, []);
});

test("the choice is offered in production copy, both purposes, no wire word", () => {
  assert.deepEqual(
    INTEGRATION_KEY_PURPOSES.map((option) => option.value),
    ["writer", "reader"],
    "both server purposes must be offered, push first",
  );
  for (const option of INTEGRATION_KEY_PURPOSES) {
    for (const text of [option.label, option.description, option.labelPlaceholder]) {
      assert.doesNotMatch(text, /\b(writer|reader)\b/i, `wire word leaked into copy: ${text}`);
    }
    assert.ok(option.label.endsWith(" key"), option.label);
    assert.ok(option.description.includes("cannot"), "each purpose says what it may NOT do");
  }
  assert.equal(purposeLabel("reader"), "Analytics feed key");
  assert.equal(purposeLabel("writer"), "Data push key");
});

test("a legacy row with no purpose is named as such and authorizes nothing", () => {
  assert.equal(purposeLabel(null), "No purpose recorded");
  assert.equal(purposeLabel(undefined), "No purpose recorded");
  assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: null }), true);
  assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: undefined }), true);
  assert.equal(keyAuthorizesNothing({ bankId: null, purpose: "writer" }), true);
  assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: "writer" }), false);
  assert.equal(keyAuthorizesNothing({ bankId: BANK, purpose: "reader" }), false);
});

// ---------------------------------------------------------------------------
// Parity with the generated contract
// ---------------------------------------------------------------------------

test("every field the key-issue contract carries is one the builder states", () => {
  const model = join(
    dirname(dirname(dashboardRoot())),
    "packages",
    "risk-service-api",
    "src",
    "models",
    "IntegrationKeyIssueRequest.ts",
  );
  assert.ok(existsSync(model), `generated model not found: ${model}`);
  const source = readFileSync(model, "utf8");
  const body = source.match(
    /export function IntegrationKeyIssueRequestToJSONTyped[\s\S]*?return \{([\s\S]*?)\n  \};/,
  );
  assert.ok(body, "could not read the generated serializer");
  assert.match(
    body[1],
    /purpose: /,
    "the generated serializer no longer emits purpose, so the dashboard cannot " +
      "say what a key is for and every key would be the server default again",
  );
  const serialized = [...body[1].matchAll(/value\["([A-Za-z]+)"\]/g)]
    .map((match) => match[1])
    .sort();
  assert.ok(
    serialized.length >= 5,
    `read only ${serialized.length} fields from the generated serializer — the ` +
      `shape it is parsed out of has changed, so this test is no longer reading ` +
      `the contract. Fix the reader, never the assertion.`,
  );
  const stated = Object.keys(
    integrationKeyIssueRequest({ bankId: BANK, label: "x", purpose: ANALYTICS_FEED_PURPOSE }),
  ).sort();
  assert.deepEqual(
    stated,
    serialized,
    "the key-issue contract and what the dashboard states have diverged. A field " +
      "the contract carries and the builder does not set is decided by the server " +
      "default — for `purpose` that is a data push key handed to someone who asked " +
      "for an analytics feed key, reported as a success.",
  );
});

// ---------------------------------------------------------------------------
// The surfaces actually use the builder
// ---------------------------------------------------------------------------

test("the issuing hook posts through the builder and no longer hand-writes the body", () => {
  const hooks = readFileSync(join(dashboardRoot(), "lib", "api", "hooks.ts"), "utf8");
  const start = hooks.indexOf("export function useIssueIntegrationKey");
  assert.ok(start >= 0, "useIssueIntegrationKey is gone from hooks.ts");
  const end = hooks.indexOf("export function", start + 1);
  const hook = hooks.slice(start, end < 0 ? undefined : end);
  assert.ok(
    hook.includes("integrationKeyIssueRequest(draft)"),
    "useIssueIntegrationKey must build its request through integrationKeyIssueRequest, " +
      "the one place every contract field is stated",
  );
  assert.doesNotMatch(
    hook,
    /integrationKeyIssueRequest:\s*\{\s*bankId/,
    "useIssueIntegrationKey hand-writes the request body again — that is how the " +
      "purpose was dropped and every key became a push key",
  );
});

test("the issuing surface offers the purpose choice and shows each key's purpose", () => {
  const guide = readFileSync(
    join(dashboardRoot(), "components", "data-engine", "ApiPushGuide.tsx"),
    "utf8",
  );
  assert.ok(
    guide.includes("INTEGRATION_KEY_PURPOSES"),
    "ApiPushGuide no longer renders the purpose options, so an administrator " +
      "cannot issue an analytics feed key from the product",
  );
  assert.ok(
    guide.includes("purposeLabel("),
    "ApiPushGuide no longer names each listed key's purpose, so an administrator " +
      "cannot tell which kind of key they hold",
  );
});

if (failures > 0) {
  console.error(`${failures} integrationKeys test(s) failed`);
  process.exit(1);
}
console.log("integrationKeys.test.ts: all tests passed");
