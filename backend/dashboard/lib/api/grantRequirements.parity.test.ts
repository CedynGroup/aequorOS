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
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  CHAIN_DECISION_MODULE,
  CHAIN_DECISION_SENSITIVITY,
  dataScopeShortfall,
  grantShortfall,
  overlappingGrantNotice,
  type HeldGrant,
} from "./grantRequirements";
import {
  BOOK_COVERAGE_OPTIONS,
  DATA_SCOPE_KIND_FIELD,
  DATA_SCOPE_VALUES_FIELD,
  grantScopeDisplay,
  grantScopeRefusal,
  MAX_DATA_SCOPE_VALUES,
  WHOLE_INSTITUTION_BOOK,
  type GrantDraft,
} from "./grants";

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

/**
 * The migration that created the data-scope columns. The contract
 * (`.ai/BI_PHASE4_CONTRACT.md`) names it authoritative and names its CHECK
 * verbatim, so it — not a prose summary — is what this mirror is read against.
 */
const DATA_SCOPE_MIGRATION = join(
  repoRoot(),
  "backend",
  "alembic",
  "versions",
  "202609270073_authorization_data_scopes.py",
);

/** The request and response contracts the composer posts to and reads from. */
const AUTHORIZATION_SCHEMAS = join(
  repoRoot(),
  "backend",
  "app",
  "schemas",
  "authorization.py",
);

function draft(over: Partial<GrantDraft>): GrantDraft {
  return {
    roleBundle: "approver" as GrantDraft["roleBundle"],
    institutionScope: "institution" as GrantDraft["institutionScope"],
    institutionId: "BK-SAMP0001",
    moduleScope: "all" as GrantDraft["moduleScope"],
    sensitivityScope: "all" as GrantDraft["sensitivityScope"],
    dataScope: WHOLE_INSTITUTION_BOOK,
    reasonCategory: "other",
    reasonDetail: "test",
    reference: "",
    validUntil: "",
    ...over,
  };
}

test("the mirrored gate equals the backend's CHAIN_DECISION_GATE", () => {
  const backendRoot = join(repoRoot(), "backend");
  const [backendModule, backendSensitivity] = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "--frozen",
        "python",
        "-c",
        `import json
from app.services.regulatory_reporting.family_access import CHAIN_DECISION_GATE
print(json.dumps([CHAIN_DECISION_GATE.module.value, CHAIN_DECISION_GATE.sensitivity.value]))`,
      ],
      {
        cwd: backendRoot,
        env: {
          ...process.env,
          PYTHONPATH: backendRoot,
          APP_ENV: "test",
          DATABASE_URL: "",
        },
        encoding: "utf8",
      },
    ),
  ) as [string, string];
  assert.equal(
    CHAIN_DECISION_MODULE,
    backendModule,
    "the mirrored module has drifted from the backend gate",
  );
  assert.equal(
    CHAIN_DECISION_SENSITIVITY,
    backendSensitivity,
    "the mirrored sensitivity has drifted from the backend gate",
  );
});

test("the case that cost a session: Approver at Confidential is inert", () => {
  const warning = grantShortfall(
    draft({
      sensitivityScope: "confidential" as GrantDraft["sensitivityScope"],
    }),
  );
  assert.ok(warning, "an Approver at Confidential must warn");
  assert.match(warning, /Restricted/);
  assert.match(warning, /approve returns/);
});

test("a sound grant says nothing", () => {
  assert.equal(grantShortfall(draft({})), null);
  assert.equal(
    grantShortfall(
      draft({
        sensitivityScope: "restricted" as GrantDraft["sensitivityScope"],
      }),
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
    overlappingGrantNotice(draft({}), [{ ...heldApprover, status: "revoked" }]),
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
    overlappingGrantNotice(orgDraft, [
      { ...heldApprover, institutionId: null },
    ]),
  );
});

// --- the data-scope mirror ---------------------------------------------------

test("the wire field names are the migration's column names", () => {
  assert.ok(
    existsSync(DATA_SCOPE_MIGRATION),
    `the data-scope migration is missing: ${DATA_SCOPE_MIGRATION}. The ` +
      `composer cannot post a column nobody created.`,
  );
  const source = readFileSync(DATA_SCOPE_MIGRATION, "utf8");
  for (const column of [DATA_SCOPE_KIND_FIELD, DATA_SCOPE_VALUES_FIELD]) {
    assert.match(
      source,
      new RegExp(`sa\\.Column\\(\\s*\\n?\\s*"${column}"`),
      `the composer posts '${column}', which the migration does not add. A ` +
        `field name the server does not recognise is silently dropped by ` +
        `Pydantic's extra="forbid" as a 422 — or worse, accepted and ignored.`,
    );
  }
});

test("the three choices are the three storable kinds", () => {
  const source = readFileSync(DATA_SCOPE_MIGRATION, "utf8");
  const kinds = source.match(/_KINDS\s*=\s*\(([^)]*)\)/);
  assert.ok(kinds, "could not find the kind vocabulary in the migration");
  const declared = [...kinds[1].matchAll(/"([a-z_]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    [...BOOK_COVERAGE_OPTIONS.map(([kind]) => kind)].sort(),
    [...declared].sort(),
    "the composer offers a set of coverages that is not the set the column " +
      "admits — either an unstorable choice is on screen, or a storable one " +
      "cannot be granted at all",
  );
});

test("the refusal mirrors the CHECK, both halves", () => {
  const source = readFileSync(DATA_SCOPE_MIGRATION, "utf8");
  // Half one: `all` carries no list. Half two: any other kind carries a
  // non-empty one. Read from the migration so a reshaped CHECK is noticed here.
  assert.match(source, /data_scope_kind = 'all' AND data_scope_values IS NULL/);
  assert.match(source, /json_array_length\(data_scope_values\) > 0/);

  // Half two, mirrored: a narrowing kind with nothing chosen is refused.
  for (const kind of ["branch", "region"] as const) {
    const refusal = grantScopeRefusal(
      draft({ dataScope: { kind, values: [] } }),
    );
    assert.ok(
      refusal,
      `${kind} coverage with nothing chosen must be refused in the composer`,
    );
    assert.match(refusal, /at least one/);
  }
  assert.equal(
    grantScopeRefusal(
      draft({ dataScope: { kind: "branch", values: ["001"] } }),
    ),
    null,
    "one chosen branch is a complete coverage",
  );
  // Half one, mirrored: `all` never carries a list, so it is never refused.
  assert.equal(
    grantScopeRefusal(draft({ dataScope: { kind: "all", values: ["001"] } })),
    null,
  );
});

test("the request contract names both fields, and the cap is mirrored", () => {
  assert.ok(
    existsSync(AUTHORIZATION_SCHEMAS),
    `the grant contract is missing: ${AUTHORIZATION_SCHEMAS}`,
  );
  const source = readFileSync(AUTHORIZATION_SCHEMAS, "utf8");
  assert.match(
    source,
    new RegExp(`${DATA_SCOPE_KIND_FIELD}: DataScope`),
    "ScopedGrantInput no longer takes the coverage kind the composer posts",
  );
  const values = source.match(
    new RegExp(
      `${DATA_SCOPE_VALUES_FIELD}: list\\[str\\][\\s\\S]{0,240}?max_length=(\\d+)`,
    ),
  );
  assert.ok(values, "could not read the coverage value field and its cap");
  assert.equal(
    String(MAX_DATA_SCOPE_VALUES),
    values[1],
    "the composer's cap on how many branches one grant may name has drifted " +
      "from the server's, so it either refuses a grant the server accepts or " +
      "posts one it will not",
  );
});

test("the branch directory is read by the response model's own field names", () => {
  const source = readFileSync(AUTHORIZATION_SCHEMAS, "utf8");
  const entry = source.match(
    /class BranchDirectoryEntryRead\(ClosedModel\):([\s\S]*?)\n\nclass /,
  );
  assert.ok(entry, "could not find BranchDirectoryEntryRead");
  const fields = [...entry[1].matchAll(/^\s{4}([a-z_]+):/gm)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    [...fields].sort(),
    ["code", "name", "region"],
    "the branch row's fields changed; `parseBranchDirectory` reads exactly " +
      "these three and treats anything else as a protocol failure, so it would " +
      "start reporting every institution as having no branch register",
  );
  const directory = source.match(
    /class BranchDirectoryRead\(ClosedModel\):([\s\S]*?)(?:\nclass |$)/,
  );
  assert.ok(directory);
  assert.match(directory[1], /branches: list\[BranchDirectoryEntryRead\]/);
  assert.match(directory[1], /regions: list\[str\]/);
});

test("the stored-scope label the list shows is a field the server sends", () => {
  const source = readFileSync(AUTHORIZATION_SCHEMAS, "utf8");
  // `grantScopeDisplay` prefers this over its own wording. If it were dropped
  // the fallback would still describe every grant, but silently — so the
  // preference is pinned rather than assumed.
  assert.match(source, /data_scope_label: str/);
  assert.equal(
    grantScopeDisplay({
      data_scope_kind: "region",
      data_scope_values: ["Northern"],
      data_scope_label: "Selected regions: Northern",
    }).label,
    "Selected regions: Northern",
  );
});

test("a narrowed coverage warns about the figures it cannot answer", () => {
  const warning = dataScopeShortfall(
    draft({ dataScope: { kind: "branch", values: ["001"] } }),
  );
  assert.ok(warning, "a branch-scoped grant must say what it does not include");
  assert.match(warning, /institution as a whole/);
  assert.match(warning, /second grant/);
  assert.ok(
    dataScopeShortfall(
      draft({ dataScope: { kind: "region", values: ["Northern"] } }),
    ),
  );
});

test("the whole book, and organization-wide coverage, warn about nothing", () => {
  assert.equal(dataScopeShortfall(draft({})), null);
  assert.equal(
    dataScopeShortfall(
      draft({
        institutionScope: "organization" as GrantDraft["institutionScope"],
        institutionId: undefined,
        // Even if a stale draft still carries branch codes, an organization-wide
        // sentence cannot state them — so there is nothing to warn about.
        dataScope: { kind: "branch", values: ["001"] },
      }),
    ),
    null,
  );
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("grantRequirements: all tests passed");
