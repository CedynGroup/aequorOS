/**
 * The grant sentence stays scalar, and its coverage says what it means.
 *
 * Phase 4 gave the composer a fifth dimension — which slice of an institution's
 * book the grant admits — and every assertion below exists because one specific
 * way of getting it wrong would be invisible on screen:
 *
 * - a coverage the composer cannot POST would be granted as the whole book,
 *   with a success dialog (the generated client drops fields it was not
 *   generated from, so the builder's shape is pinned against the generated
 *   request contract here — every field that contract carries must be one this
 *   composer decides, or the server default decides it instead);
 * - a branch list that survives a change of institution would name nothing;
 * - a branch request that FAILED would look like a bank that declared nothing;
 * - a grant rendered without its coverage reads as institution-wide;
 * - a cache key missing the institution would hand the composer a sibling
 *   bank's branches.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { ModuleScope } from "@aequoros/risk-service-api";
import {
  BOOK_COVERAGE_OPTIONS,
  BranchDirectoryShapeError,
  bookCoverageAvailability,
  bookCoverageChoiceAvailable,
  canAddGrantToMember,
  compactGrantFragment,
  coverageDirectory,
  DATA_SCOPE_KIND_FIELD,
  DATA_SCOPE_VALUES_FIELD,
  draftScopeLabel,
  grantCreateRequest,
  grantPreviewFingerprint,
  grantPreviewRequest,
  grantScopeDisplay,
  grantScopeRefusal,
  institutionBranchesKey,
  MODULE_OPTIONS,
  parseBranchDirectory,
  ssoApprovalRequest,
  statedDataScope,
  visibleGrantFragments,
  WHOLE_INSTITUTION_BOOK,
  type BranchDirectory,
  type GrantDataScope,
  type GrantDraft,
} from "./grants";
import { queryAuthorityScope } from "./queryPolicy";

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

const MEMBER = "6f1c0c62-6d5e-4f3f-8f3a-2b2d9a1f5c11";
const BANK_A = "BK-SAMP0001";
const BANK_B = "BK-SAMP0002";

function draft(over: Partial<GrantDraft> = {}): GrantDraft {
  return {
    roleBundle: "analyst" as GrantDraft["roleBundle"],
    institutionScope: "institution" as GrantDraft["institutionScope"],
    institutionId: BANK_A,
    moduleScope: "liq" as GrantDraft["moduleScope"],
    sensitivityScope: "confidential" as GrantDraft["sensitivityScope"],
    dataScope: WHOLE_INSTITUTION_BOOK,
    reason: "Treasury monitoring responsibilities",
    ...over,
  };
}

function branchScope(...values: string[]): GrantDataScope {
  return { kind: "branch", values };
}

const DIRECTORY: BranchDirectory = {
  institutionId: BANK_A,
  branches: [
    { code: "001", name: "Head Office", region: "Coastal" },
    { code: "002", name: "Second Branch", region: "Northern" },
    { code: "003", name: "Third Branch", region: null },
  ],
  regions: ["Coastal", "Northern"],
};

// --- the sentence is still scalar -------------------------------------------

test("every authority dimension but the coverage list is a scalar", () => {
  const composed = draft();
  assert.equal(Array.isArray(composed.roleBundle), false);
  assert.equal(Array.isArray(composed.institutionId), false);
  assert.equal(Array.isArray(composed.moduleScope), false);
  assert.equal(Array.isArray(composed.sensitivityScope), false);
  assert.equal(Array.isArray(composed.dataScope.kind), false);
  // The values ARE a list, because the column is a list — and it narrows ONE
  // binding. What must never appear is an array-shaped ROLE, MODULE, SENSITIVITY
  // or INSTITUTION, which could fan one request out into a Cartesian product.
  assert.equal(Array.isArray(composed.dataScope.values), true);
});

test("the three choices are offered in production copy, not a vocabulary", () => {
  for (const [kind, label] of BOOK_COVERAGE_OPTIONS) {
    assert.notEqual(
      label.toLowerCase(),
      kind,
      `the coverage choice for '${kind}' renders the wire value itself`,
    );
    assert.ok(
      label.length > 8 && /^[A-Z]/.test(label),
      `'${label}' does not read as a choice an Org Owner would recognise`,
    );
    assert.equal(
      /_/.test(label),
      false,
      `'${label}' carries an identifier, not words`,
    );
  }
});

// --- the three choices produce the three requests ---------------------------
//
// The builders return the GENERATED request models, so the wire names are the
// serializer's business and these assertions are about the decision each field
// carries. An unset optional field is what the whole book means: the generated
// serializer emits `undefined` for it and `JSON.stringify` drops the key, so
// the request omits the column and the server default applies.

test("the whole book posts no coverage fields at all", () => {
  const body = grantCreateRequest(draft(), MEMBER, "sentence");
  assert.equal(body.dataScopeKind, undefined);
  assert.equal(body.dataScopeValues, undefined);
  assert.equal(JSON.stringify(body).includes("dataScope"), false);
  assert.equal(body.institutionId, BANK_A);
  assert.equal(body.roleBundle, "analyst");
  assert.equal(body.expectedAuthoritySentence, "sentence");
  assert.equal(body.principalUserId, MEMBER);
});

test("selected branches post the kind and the codes", () => {
  const body = grantCreateRequest(
    draft({ dataScope: branchScope("002", "001") }),
    MEMBER,
    "sentence",
  );
  assert.equal(body.dataScopeKind, "branch");
  // Sorted and de-duplicated so the same choice is always the same request.
  assert.deepEqual(body.dataScopeValues, ["001", "002"]);
});

test("selected regions post the declared names", () => {
  const body = grantPreviewRequest(
    draft({ dataScope: { kind: "region", values: ["Northern", "Northern"] } }),
    MEMBER,
  );
  assert.equal(body.dataScopeKind, "region");
  assert.deepEqual(body.dataScopeValues, ["Northern"]);
  assert.equal("expectedAuthoritySentence" in body, false);
});

test("the SSO approval body is the same sentence without the user", () => {
  const body = ssoApprovalRequest(
    draft({ dataScope: branchScope("001") }),
    "sentence",
  );
  assert.equal("principalUserId" in body, false);
  assert.equal(body.dataScopeKind, "branch");
  assert.equal(body.expectedAuthoritySentence, "sentence");
});

test("blank and duplicate values never reach the wire", () => {
  const body = grantCreateRequest(
    draft({ dataScope: branchScope(" 001 ", "001", "") }),
    MEMBER,
    "s",
  );
  assert.deepEqual(body.dataScopeValues, ["001"]);
});

// --- the two validation refusals, mirroring the server ----------------------

test("a narrowing coverage with nothing chosen is refused, by name", () => {
  const branches = grantScopeRefusal(draft({ dataScope: branchScope() }));
  assert.ok(branches);
  assert.match(branches, /at least one branch/);
  const regions = grantScopeRefusal(
    draft({ dataScope: { kind: "region", values: [] } }),
  );
  assert.ok(regions);
  assert.match(regions, /at least one region/);
  // And a caller that ignored the refusal still cannot widen the grant by
  // accident: the body NAMES the narrowing kind with an empty list, which is
  // exactly the shape the database refuses — so the request fails and nothing
  // is granted. Omitting the pair here would have meant the whole book.
  const body = grantCreateRequest(
    draft({ dataScope: branchScope() }),
    MEMBER,
    "s",
  );
  assert.equal(body.dataScopeKind, "branch");
  assert.deepEqual(body.dataScopeValues, []);
});

test("the whole book never carries a list", () => {
  const scope = statedDataScope(
    draft({ dataScope: { kind: "all", values: ["001"] } }),
  );
  assert.equal(scope.kind, "all");
  assert.deepEqual([...scope.values], []);
});

// --- the organization-wide case ---------------------------------------------

test("an organization-wide grant cannot state a branch list", () => {
  const orgDraft = draft({
    institutionScope: "organization" as GrantDraft["institutionScope"],
    institutionId: undefined,
    dataScope: branchScope("001"),
  });
  const body = grantCreateRequest(orgDraft, MEMBER, "s");
  assert.equal(body.dataScopeKind, undefined);
  // The server's own validator forbids an institution id here; sending one
  // would be a 422 rather than a wider grant, but it must not be sent at all —
  // and an unset field is dropped from the request, not posted as null.
  assert.equal(body.institutionId, undefined);
  assert.equal(JSON.stringify(body).includes("institutionId"), false);
  assert.equal(statedDataScope(orgDraft).kind, "all");

  const availability = bookCoverageAvailability({
    institutionScope: "organization",
    directory: DIRECTORY,
    failed: false,
    loading: false,
  });
  assert.equal(availability.status, "organization_wide");
  assert.equal(bookCoverageChoiceAvailable(availability, "branch"), false);
  assert.equal(bookCoverageChoiceAvailable(availability, "region"), false);
  assert.equal(bookCoverageChoiceAvailable(availability, "all"), true);
  assert.equal(coverageDirectory(availability), null);
  if ("reason" in availability) {
    assert.match(availability.reason, /branch lists differ/);
  }
});

// --- what the bank has, and has not, declared -------------------------------

test("a bank with no branch register is told so, and offers nothing to pick", () => {
  const availability = bookCoverageAvailability({
    institutionScope: "institution",
    directory: { institutionId: BANK_A, branches: [], regions: [] },
    failed: false,
    loading: false,
  });
  assert.equal(availability.status, "no_register");
  assert.equal(bookCoverageChoiceAvailable(availability, "branch"), false);
  assert.equal(bookCoverageChoiceAvailable(availability, "region"), false);
  assert.equal(coverageDirectory(availability), null);
  if ("reason" in availability) {
    assert.match(availability.reason, /has not supplied a branch register/);
  }
});

test("a bank that declares no regions can still be narrowed by branch", () => {
  const availability = bookCoverageAvailability({
    institutionScope: "institution",
    directory: {
      institutionId: BANK_A,
      branches: [{ code: "001", name: "Head Office", region: null }],
      regions: [],
    },
    failed: false,
    loading: false,
  });
  assert.equal(availability.status, "branches_only");
  assert.equal(bookCoverageChoiceAvailable(availability, "branch"), true);
  // Never an empty region picker: a region is DECLARED on the register and
  // never inferred, so a bank that has not declared one has none.
  assert.equal(bookCoverageChoiceAvailable(availability, "region"), false);
  assert.ok(coverageDirectory(availability));
  if ("reason" in availability) {
    assert.match(availability.reason, /declares no regions/);
  }
});

test("a readable register offers both narrowings", () => {
  const availability = bookCoverageAvailability({
    institutionScope: "institution",
    directory: DIRECTORY,
    failed: false,
    loading: false,
  });
  assert.equal(availability.status, "ready");
  for (const [kind] of BOOK_COVERAGE_OPTIONS) {
    assert.equal(bookCoverageChoiceAvailable(availability, kind), true);
  }
});

// --- a failed request fails closed ------------------------------------------

test("a failed branch request is not a bank that declared nothing", () => {
  const availability = bookCoverageAvailability({
    institutionScope: "institution",
    directory: null,
    failed: true,
    loading: false,
  });
  assert.equal(availability.status, "unavailable");
  assert.notEqual(
    availability.status,
    "no_register",
    "a request that did not answer must never be reported as a bank that " +
      "supplied nothing — that sends an Org Owner to their data engineers " +
      "about a problem in this browser",
  );
  assert.equal(bookCoverageChoiceAvailable(availability, "branch"), false);
  assert.equal(bookCoverageChoiceAvailable(availability, "region"), false);
  // The whole book stays selectable: it is what every grant meant before this
  // feature existed. What must not happen is it looking like the only sensible
  // choice with no explanation, so the state carries the reason.
  assert.equal(bookCoverageChoiceAvailable(availability, "all"), true);
  if ("reason" in availability) {
    assert.match(availability.reason, /could not be loaded/);
    assert.match(availability.reason, /whole book/);
  } else {
    throw new Error("the failure must state why coverage cannot be narrowed");
  }
});

test("a failure outranks stale data and a pending load", () => {
  assert.equal(
    bookCoverageAvailability({
      institutionScope: "institution",
      directory: DIRECTORY,
      failed: true,
      loading: false,
    }).status,
    "unavailable",
  );
  assert.equal(
    bookCoverageAvailability({
      institutionScope: "institution",
      directory: null,
      failed: false,
      loading: true,
    }).status,
    "loading",
  );
  // No directory and no error yet is still not an empty register.
  assert.equal(
    bookCoverageAvailability({
      institutionScope: "institution",
      directory: null,
      failed: false,
      loading: false,
    }).status,
    "loading",
  );
});

test("an unreadable response is an error, never an empty directory", () => {
  assert.throws(
    () => parseBranchDirectory({ unexpected: true }, BANK_A),
    BranchDirectoryShapeError,
  );
  assert.throws(
    () => parseBranchDirectory({ branches: [{ name: "No code" }] }, BANK_A),
    BranchDirectoryShapeError,
  );
});

test("the directory is parsed from the route's own field names", () => {
  const parsed = parseBranchDirectory(
    {
      institution_id: BANK_A,
      branches: [
        { code: "001", name: "Head Office", region: "Coastal" },
        { code: "002", name: "Second Branch", region: null },
      ],
    },
    BANK_A,
  );
  assert.deepEqual(
    parsed.branches.map((branch) => branch.code),
    ["001", "002"],
  );
  assert.equal(parsed.branches[0].name, "Head Office");
  assert.equal(parsed.branches[1].region, null);
  // The register's own aliases are resolved server-side, so a row spelt the
  // register's way is a protocol failure here — never a branch to drop quietly.
  assert.throws(
    () =>
      parseBranchDirectory(
        { branches: [{ business_unit_id: "001", business_unit_name: "X" }] },
        BANK_A,
      ),
    BranchDirectoryShapeError,
  );
  // Regions come from the response when it lists them, and otherwise from the
  // branches' own declared values. Either way, only DECLARED values.
  assert.deepEqual([...parsed.regions], ["Coastal"]);
  assert.deepEqual(
    [
      ...parseBranchDirectory(
        { branches: [{ code: "001", name: "A" }], regions: ["Given"] },
        BANK_A,
      ).regions,
    ],
    ["Given"],
  );
  assert.deepEqual(
    [...parseBranchDirectory({ branches: [] }, BANK_A).regions],
    [],
  );
});

// --- cache identity ----------------------------------------------------------

const TENANT_A = "OR-DEM00001";
const TENANT_B = "OR-OTHER001";
const owner = queryAuthorityScope(TENANT_A, "owner@aequoros.example", 7);

function keyText(value: readonly unknown[]): string {
  return JSON.stringify(value);
}

function isPrefixOf(prefix: readonly unknown[], candidate: readonly unknown[]) {
  if (prefix.length > candidate.length) return false;
  return prefix.every(
    (part, index) => JSON.stringify(part) === JSON.stringify(candidate[index]),
  );
}

function assertDistinct(
  dimension: string,
  left: readonly unknown[],
  right: readonly unknown[],
): void {
  assert.notEqual(
    keyText(left),
    keyText(right),
    `${dimension} must change the cache key`,
  );
  assert.equal(
    isPrefixOf(left, right),
    false,
    `${dimension}: one key must not be a prefix of the other`,
  );
  assert.equal(
    isPrefixOf(right, left),
    false,
    `${dimension}: one key must not be a prefix of the other`,
  );
}

test("the branch directory is keyed per tenant, actor, generation and bank", () => {
  const base = institutionBranchesKey(owner, BANK_A) as readonly unknown[];
  assertDistinct(
    "organization",
    base,
    institutionBranchesKey(
      queryAuthorityScope(TENANT_B, "owner@aequoros.example", 7),
      BANK_A,
    ) as readonly unknown[],
  );
  assertDistinct(
    "signed-in actor",
    base,
    institutionBranchesKey(
      queryAuthorityScope(TENANT_A, "other@aequoros.example", 7),
      BANK_A,
    ) as readonly unknown[],
  );
  assertDistinct(
    "authorization generation",
    base,
    institutionBranchesKey(
      queryAuthorityScope(TENANT_A, "owner@aequoros.example", 8),
      BANK_A,
    ) as readonly unknown[],
  );
  assertDistinct(
    "institution",
    base,
    institutionBranchesKey(owner, BANK_B) as readonly unknown[],
  );
  // An unresolved institution is its own question, never a match for the first
  // bank that resolves.
  assertDistinct(
    "pending institution",
    institutionBranchesKey(owner, null) as readonly unknown[],
    base,
  );
  assert.equal(
    keyText(institutionBranchesKey(owner, BANK_A) as readonly unknown[]),
    keyText(base),
    "the same question must be the same key",
  );
  assert.equal(base[1], TENANT_A, "the tenant rides in position 1");
  assert.equal(base[2], owner.authorityId, "the actor and generation in 2");
  assert.equal(base[3], BANK_A, "the institution in 3");
});

test("the preview fingerprint moves with the coverage", () => {
  const base = grantPreviewFingerprint(draft(), MEMBER);
  assert.notEqual(
    base,
    grantPreviewFingerprint(draft({ dataScope: branchScope("001") }), MEMBER),
    "a narrowed coverage is a different sentence and needs a new preview",
  );
  assert.notEqual(
    grantPreviewFingerprint(draft({ dataScope: branchScope("001") }), MEMBER),
    grantPreviewFingerprint(
      draft({ dataScope: branchScope("001", "002") }),
      MEMBER,
    ),
  );
  assert.equal(
    grantPreviewFingerprint(
      draft({ dataScope: branchScope("002", "001") }),
      MEMBER,
    ),
    grantPreviewFingerprint(
      draft({ dataScope: branchScope("001", "002") }),
      MEMBER,
    ),
    "the same set of branches is the same sentence",
  );
});

// --- every module scope is grantable ---------------------------------------

// The composer offers every module scope the generated contract accepts: a
// value added to the backend vocabulary without a label here would be
// ungrantable from Members. This is a compile-time check — the record below
// has a required key for each scope the options leave out.
type OfferedModuleScope = (typeof MODULE_OPTIONS)[number][0];
const everyModuleScopeIsOffered: Record<
  Exclude<ModuleScope, OfferedModuleScope>,
  never
> = {};

test("every module scope is offered once, under its label", () => {
  assert.deepEqual(everyModuleScopeIsOffered, {});
  const offered = MODULE_OPTIONS.map(([moduleScope]) => moduleScope);
  assert.equal(new Set(offered).size, offered.length);
  for (const [moduleScope, label] of [
    ["credit", "Credit"],
    ["institution", "Institution Profile"],
  ] as const) {
    assert.equal(
      compactGrantFragment({
        effective: true,
        roleBundle: "viewer",
        moduleScope,
        institutionScope: "institution",
        institutionName: "Aequor Bank Ghana",
      } as never),
      `Viewer · ${label} · Aequor Bank Ghana`,
    );
  }
});

// --- the members list shows what was granted --------------------------------

test("every coverage kind renders as a sentence, not an enum", () => {
  const cases: readonly (readonly [unknown, RegExp, boolean])[] = [
    [{}, /whole book/, false],
    [{ data_scope_kind: "all", data_scope_values: null }, /whole book/, false],
    [
      { data_scope_kind: "branch", data_scope_values: ["001", "002"] },
      /Selected branches: 001, 002/,
      true,
    ],
    [
      { dataScopeKind: "branch", dataScopeValues: ["001"] },
      /Selected branches: 001/,
      true,
    ],
    [
      { data_scope_kind: "region", data_scope_values: ["Coastal"] },
      /Selected regions: Coastal/,
      true,
    ],
    // An unfamiliar kind is read as NARROWED, never as the whole book: it is
    // the one direction an access display must not be wrong in.
    [
      { data_scope_kind: "future_kind", data_scope_values: ["x"] },
      /cannot describe/,
      true,
    ],
    [
      { data_scope_kind: "branch", data_scope_values: [] },
      /cannot describe/,
      true,
    ],
    [
      { data_scope_kind: "branch", data_scope_values: null },
      /cannot describe/,
      true,
    ],
  ];
  for (const [grant, expected, narrowed] of cases) {
    const display = grantScopeDisplay(grant);
    assert.match(display.label, expected);
    assert.equal(
      display.narrowed,
      narrowed,
      `narrowed for ${JSON.stringify(grant)}`,
    );
    assert.equal(
      /\ball\b|\bbranch\b|\bregion\b/.test(display.label.toLowerCase()) &&
        display.label.includes("data_scope"),
      false,
      "no raw wire field may reach the screen",
    );
    if (narrowed) {
      assert.ok(display.fragment, "a narrowed grant needs a row fragment");
    } else {
      assert.equal(display.fragment, null);
    }
  }
  assert.match(
    grantScopeDisplay({
      data_scope_kind: "branch",
      data_scope_values: ["001", "002", "003", "004", "005"],
    }).label,
    /and 2 more/,
  );
});

test("the server's own label wins wherever the row carries one", () => {
  // Same rule as the authority sentence: the server owns the copy that
  // describes stored authority, so no screen can word a grant differently from
  // the record of it.
  assert.equal(
    grantScopeDisplay({
      data_scope_kind: "branch",
      data_scope_values: ["001"],
      data_scope_label: "Selected branches: 001",
    }).label,
    "Selected branches: 001",
  );
  assert.equal(
    grantScopeDisplay({
      dataScopeKind: "all",
      dataScopeValues: [],
      dataScopeLabel: "Whole institution",
    }).label,
    "Whole institution",
  );
  // A label does not make an unfamiliar kind read as the whole book.
  const odd = grantScopeDisplay({
    data_scope_kind: "future_kind",
    data_scope_values: [],
    data_scope_label: "Something new",
  });
  assert.equal(odd.narrowed, true);
  assert.equal(odd.label, "Something new");
});

test("more values than one sentence may name is refused, not truncated", () => {
  const many = Array.from({ length: 501 }, (_, index) => `B${index}`);
  const refusal = grantScopeRefusal(draft({ dataScope: branchScope(...many) }));
  assert.ok(refusal, "501 branches must be refused");
  assert.match(refusal, /at most 500 branches/);
  assert.equal(
    grantScopeRefusal(draft({ dataScope: branchScope(...many.slice(0, 500)) })),
    null,
  );
});

test("the row summary carries the coverage whenever it narrows the grant", () => {
  const scoped = compactGrantFragment({
    effective: true,
    roleBundle: "analyst",
    moduleScope: "liq",
    institutionScope: "institution",
    institutionName: "Aequor Bank",
    data_scope_kind: "branch",
    data_scope_values: ["001", "002"],
  } as never);
  assert.match(scoped, /2 branches$/);
  const whole = compactGrantFragment({
    effective: true,
    roleBundle: "analyst",
    moduleScope: "liq",
    institutionScope: "institution",
    institutionName: "Aequor Bank",
  } as never);
  assert.equal(whole, "Analyst · Liquidity Monitoring · Aequor Bank");
});

test("the review step names the coverage it is about to grant", () => {
  assert.match(draftScopeLabel(draft()), /whole book/);
  assert.match(
    draftScopeLabel(draft({ dataScope: branchScope("002", "001") })),
    /Selected branches: 001, 002/,
  );
  assert.match(
    draftScopeLabel(
      draft({
        institutionScope: "organization" as GrantDraft["institutionScope"],
        institutionId: undefined,
        dataScope: branchScope("001"),
      }),
    ),
    /whole book/,
  );
});

// --- the pre-existing contract ----------------------------------------------

test("the members row still shows two grants and a remainder", () => {
  const summary = visibleGrantFragments([
    {
      effective: true,
      roleBundle: "analyst",
      moduleScope: "liq",
      institutionScope: "institution",
      institutionName: "Aequor Bank Ghana",
    },
    {
      effective: true,
      roleBundle: "viewer",
      moduleScope: "cap",
      institutionScope: "institution",
      institutionName: "Aequor Bank Ghana",
    },
    {
      effective: true,
      roleBundle: "auditor",
      moduleScope: "audit",
      institutionScope: "organization",
    },
  ] as never);
  assert.equal(summary.fragments.length, 2);
  assert.equal(summary.remaining, 1);
});

test("who may be granted to is unchanged", () => {
  assert.equal(
    canAddGrantToMember({
      authenticationMethod: "sso",
      lifecycleStatus: "deactivated",
      accessRequestState: "approval_needed",
    }),
    true,
  );
  assert.equal(
    canAddGrantToMember({
      authenticationMethod: "password",
      lifecycleStatus: "deactivated",
      accessRequestState: "none",
    }),
    false,
  );
  assert.equal(
    canAddGrantToMember({
      authenticationMethod: "service",
      lifecycleStatus: "active",
      accessRequestState: "none",
    }),
    false,
  );
});

// --- the composer actually uses all of this ---------------------------------
//
// A registered helper with no caller is an inert feature and nothing reports it
// (AGENTS.md, 2026-09-27). The first two read the composer's own source: that
// the coverage reached the screen and the wire, and that it travels through the
// generated operations with no interim transport beside them. The third reads
// the generated contract itself, and is what keeps the stale-client defect
// caught now that the interim transport that used to catch it is gone.

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

const PANEL = join(
  dashboardRoot(),
  "components",
  "settings",
  "MembersPanel.tsx",
);

test("the composer renders the control and posts through the scoped calls", () => {
  const source = readFileSync(PANEL, "utf8");
  for (const symbol of [
    "BookCoverageControl",
    "useInstitutionBranches",
    "bookCoverageAvailability",
    "grantScopeRefusal",
    "grantPreviewRequest",
    "grantCreateRequest",
    "ssoApprovalRequest",
    "grantScopeDisplay",
    "dataScopeShortfall",
  ]) {
    assert.ok(
      source.includes(symbol),
      `MembersPanel does not use ${symbol}: the coverage control exists but ` +
        `nothing on screen reaches it`,
    );
  }
});

test("the interim transport is gone, and the generated operations are used", () => {
  // The inverse of the assertion this file used to carry. While the package was
  // stale, calling these three operations would have DROPPED the coverage and
  // granted the whole book, so a hand-written transport carried the three
  // requests and a test forbade the generated names. The client has since been
  // regenerated; AGENTS.md records why the transport must not outlive that —
  // "an interim one that outlives it is a second contract nobody is checking".
  assert.equal(
    existsSync(
      join(dashboardRoot(), "components", "settings", "grantTransport.ts"),
    ),
    false,
    "components/settings/grantTransport.ts is back. It was the workaround for " +
      "a stale generated client and is now a second, unchecked copy of the " +
      "grant contract. Post through authorizationApi / authApi instead.",
  );
  const source = readFileSync(PANEL, "utf8");
  for (const operation of [
    "createAuthorizationBinding",
    "previewAuthorizationBinding",
    "authApproveSsoAccessRequest",
  ]) {
    assert.ok(
      source.includes(operation),
      `MembersPanel no longer calls ${operation}. The three grant requests ` +
        `must go through the generated operations, whose serializers are the ` +
        `one checked copy of the contract.`,
    );
  }
});

test("every field each grant contract carries is one the composer decides", () => {
  // THE PERMANENT FORM OF THE STALE-CLIENT TRIPWIRE. Each generated `ToJSON`
  // hand-enumerates its keys with no spread, so a field the builder leaves out
  // is dropped in the browser and the server's column default decides it
  // instead — silently, with a 201. That is exactly how Phase 4's coverage pair
  // would have widened a two-branch grant to the whole book. So: every property
  // a generated serializer reads must be one the matching builder states.
  //
  // ALL THREE request contracts, not the create request alone. The tripwire
  // pinned `BindingCreateRequest` only, so `SsoAccessRequestApprove` — the path
  // that admits a NEW SSO user with their first grant — and
  // `BindingPreviewRequest` could each have gained a column the composer never
  // set, and the first person to find out would have been the one whose grant
  // was wider than the Owner composed.
  //
  // The serializers' source is read from disk rather than imported, because this
  // suite runs as plain Node and the package ships TypeScript.
  const modelsDir = join(
    dirname(dirname(dashboardRoot())),
    "packages",
    "risk-service-api",
    "src",
    "models",
  );
  const contracts: readonly (readonly [string, object])[] = [
    ["BindingCreateRequest", grantCreateRequest(draft(), MEMBER, "sentence")],
    ["BindingPreviewRequest", grantPreviewRequest(draft(), MEMBER)],
    ["SsoAccessRequestApprove", ssoApprovalRequest(draft(), "sentence")],
  ];
  for (const [modelName, request] of contracts) {
    const model = join(modelsDir, `${modelName}.ts`);
    assert.ok(existsSync(model), `generated model not found: ${model}`);
    const source = readFileSync(model, "utf8");
    const body = source.match(
      new RegExp(
        `export function ${modelName}ToJSONTyped[\\s\\S]*?return \\{([\\s\\S]*?)\\n  \\};`,
      ),
    );
    assert.ok(body, `could not read the generated serializer for ${modelName}`);
    // Both coverage columns first, and by name: a client regenerated against a
    // schema that dropped either one would stop sending it, and this is the
    // failure whose cause is worth naming outright.
    for (const wireName of [DATA_SCOPE_KIND_FIELD, DATA_SCOPE_VALUES_FIELD]) {
      assert.match(
        body[1],
        new RegExp(`${wireName}: `),
        `${modelName}'s generated serializer no longer emits ${wireName}, so the ` +
          `composer cannot post the coverage and every grant would be the whole book`,
      );
    }
    const serialized = [...body[1].matchAll(/value\["([A-Za-z]+)"\]/g)]
      .map((match) => match[1])
      .sort();
    assert.ok(
      serialized.length >= 9,
      `read only ${serialized.length} fields from ${modelName}'s serializer — ` +
        `the shape it is parsed out of has changed, so this test is no longer ` +
        `reading the contract. Fix the reader, never the assertion.`,
    );
    const stated = Object.keys(request).sort();
    assert.deepEqual(
      stated,
      serialized,
      `${modelName} and what the composer states have diverged. A field the ` +
        "contract carries and the composer does not set is decided by the " +
        "server default, which for a scope column means a WIDER grant than " +
        "the Owner composed.",
    );
  }
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("grants.test.ts: the grant sentence and its coverage hold.");
