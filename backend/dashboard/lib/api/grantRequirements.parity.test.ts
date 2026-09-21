/**
 * The grant warning must mirror the backend gate it warns about.
 *
 * `CHAIN_DECISION_GATE` in
 * `backend/app/services/regulatory_reporting/family_access.py` is the single
 * authority on what a filing-chain decision is evaluated against. This file
 * reads it and fails if the dashboard's mirror has drifted — because a warning
 * that names the wrong requirement is worse than none: it would send an Org
 * Owner to re-issue a grant that was already correct, or bless one that is not.
 *
 * Same shape as `components/icaap/editor/schema.parity.test.ts`. When the
 * backend gate changes, fix the mirror — never loosen this test.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  CHAIN_DECISION_MODULE,
  CHAIN_DECISION_SENSITIVITY,
  grantShortfall,
  overlappingGrantNotice,
  type HeldGrant,
} from "./grantRequirements";
import type { GrantDraft } from "./grants";

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

/** The repository root, found by walking up to the directory holding `backend/`. */
function repoRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 10; i += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("could not locate the repository root from " + __dirname);
}

const GATE_SOURCE = join(
  repoRoot(),
  "backend",
  "app",
  "services",
  "regulatory_reporting",
  "family_access.py",
);

function draft(over: Partial<GrantDraft>): GrantDraft {
  return {
    roleBundle: "approver" as GrantDraft["roleBundle"],
    institutionScope: "institution" as GrantDraft["institutionScope"],
    institutionId: "BK-SAMP0001",
    moduleScope: "all" as GrantDraft["moduleScope"],
    sensitivityScope: "all" as GrantDraft["sensitivityScope"],
    reason: "test",
    ...over,
  };
}

test("the mirrored gate equals the backend's CHAIN_DECISION_GATE", () => {
  assert.ok(
    existsSync(GATE_SOURCE),
    `the backend gate is missing: ${GATE_SOURCE}. The dashboard cannot warn ` +
      `about a requirement it cannot read.`,
  );
  const source = readFileSync(GATE_SOURCE, "utf8");
  const match = source.match(
    /CHAIN_DECISION_GATE[^=]*=\s*FamilyGate\(\s*module=Module\.(\w+),\s*sensitivity=Sensitivity\.(\w+)\s*\)/,
  );
  assert.ok(
    match,
    "could not find CHAIN_DECISION_GATE in family_access.py — if it was " +
      "renamed or reshaped, update this test AND the mirror together.",
  );
  const [, backendModule, backendSensitivity] = match;
  assert.equal(
    CHAIN_DECISION_MODULE,
    // Module.REGULATORY -> the wire/scope value the dashboard sends.
    backendModule === "REGULATORY" ? "reg" : backendModule.toLowerCase(),
    "the mirrored module has drifted from the backend gate",
  );
  assert.equal(
    CHAIN_DECISION_SENSITIVITY,
    backendSensitivity.toLowerCase(),
    "the mirrored sensitivity has drifted from the backend gate",
  );
});

test("the case that cost a session: Approver at Confidential is inert", () => {
  const warning = grantShortfall(
    draft({ sensitivityScope: "confidential" as GrantDraft["sensitivityScope"] }),
  );
  assert.ok(warning, "an Approver at Confidential must warn");
  assert.match(warning, /Restricted/);
  assert.match(warning, /approve returns/);
});

test("a sound grant says nothing", () => {
  assert.equal(grantShortfall(draft({})), null);
  assert.equal(
    grantShortfall(
      draft({ sensitivityScope: "restricted" as GrantDraft["sensitivityScope"] }),
    ),
    null,
  );
  assert.equal(
    grantShortfall(draft({ moduleScope: "reg" as GrantDraft["moduleScope"] })),
    null,
  );
});

test("a narrow module is caught too, and both misses are named together", () => {
  const warning = grantShortfall(
    draft({
      moduleScope: "liq" as GrantDraft["moduleScope"],
      sensitivityScope: "aggregated" as GrantDraft["sensitivityScope"],
    }),
  );
  assert.ok(warning);
  assert.match(warning, /Regulatory Reporting/);
  assert.match(warning, /Restricted/);
});

test("the Validator bundle is covered — it needs Restricted too", () => {
  // The founder would have hit this a second time on the Validator grant.
  const warning = grantShortfall(
    draft({
      roleBundle: "validator" as GrantDraft["roleBundle"],
      sensitivityScope: "confidential" as GrantDraft["sensitivityScope"],
    }),
  );
  assert.ok(warning);
  assert.match(warning, /file returns with the regulator/);
});

test("bundles that do not decide on returns are left alone", () => {
  // An Analyst prepares through the edit path, not a chain decision, so a
  // narrower scope there is a deliberate choice rather than a mistake.
  for (const bundle of ["analyst", "viewer", "auditor", "account_admin"]) {
    assert.equal(
      grantShortfall(
        draft({
          roleBundle: bundle as GrantDraft["roleBundle"],
          sensitivityScope: "confidential" as GrantDraft["sensitivityScope"],
        }),
      ),
      null,
      `${bundle} must not be warned about a chain-decision requirement`,
    );
  }
});

const heldApprover: HeldGrant = {
  roleBundle: "approver",
  institutionId: "BK-SAMP0001",
  moduleScope: "all",
  sensitivityScope: "confidential",
  status: "active",
};

test("a second grant beside an existing one is flagged as not widening it", () => {
  // The live case: sensitivity fixed by issuing a SECOND approver row, module
  // changed at the same time, two partial rows, neither authorising anything.
  const notice = overlappingGrantNotice(draft({}), [heldApprover]);
  assert.ok(notice, "an existing same-bundle grant must be reported");
  assert.match(notice, /SEPARATE row/);
  assert.match(notice, /revoke it and issue one complete replacement/);
});

test("only same bundle on the same institution counts", () => {
  assert.equal(
    overlappingGrantNotice(draft({}), [
      { ...heldApprover, roleBundle: "viewer" },
    ]),
    null,
    "a different bundle says nothing about this draft",
  );
  assert.equal(
    overlappingGrantNotice(draft({}), [
      { ...heldApprover, institutionId: "BK-OTHER01" },
    ]),
    null,
    "a grant on a sibling institution is not this one",
  );
  assert.equal(
    overlappingGrantNotice(draft({}), [
      { ...heldApprover, status: "revoked" },
    ]),
    null,
    "a revoked row allows nothing and must not be reported",
  );
});

test("an organization-wide draft compares against organization-wide rows", () => {
  const orgDraft = draft({
    institutionScope: "organization" as GrantDraft["institutionScope"],
    institutionId: undefined,
  });
  assert.equal(
    overlappingGrantNotice(orgDraft, [heldApprover]),
    null,
    "an institution row is not the same target as an organization-wide draft",
  );
  assert.ok(
    overlappingGrantNotice(orgDraft, [{ ...heldApprover, institutionId: null }]),
  );
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("grantRequirements: all tests passed");
