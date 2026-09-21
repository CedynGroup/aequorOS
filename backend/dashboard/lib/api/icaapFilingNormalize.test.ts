/**
 * The ICAAP FILING normalisers, against the payloads a server might really
 * send.
 *
 * Same discipline as `icaapRiskCapitalNormalize.test.ts`: an empty body, a
 * body with `null` where a list was declared, a body that is not an object at
 * all. None of them may throw, and none of them may produce a permission, an
 * approval or a readiness nobody granted.
 *
 * The cases that matter most are the fail-CLOSED ones. On these screens an
 * over-generous default is not a cosmetic bug: `submittable: true` on an
 * unreadable payload offers a filing button the server will refuse, and
 * `canDecide: true` offers an officer a decision that separation of duties
 * forbids. Each of those rules has its own test below.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  normalizeArtifactVersions,
  normalizeArtifacts,
  normalizeDisclosure,
  normalizeFiling,
  normalizePackageAttachments,
  normalizePreflight,
  normalizeStages,
  normalizeWorkflowTemplates,
} from "./icaapFilingNormalize";

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

/** Everything a server could plausibly hand back that is not the contract. */
const HOSTILE: unknown[] = [
  {},
  null,
  undefined,
  [],
  "",
  0,
  "not json",
  { detail: "Not Found" },
  { error: { code: "not_found", message: "Not Found" } },
];

const NORMALIZERS: [string, (value: unknown) => unknown][] = [
  ["stages", normalizeStages],
  ["preflight", normalizePreflight],
  ["filing", normalizeFiling],
  ["package attachments", normalizePackageAttachments],
  ["artifacts", normalizeArtifacts],
  ["artifact versions", normalizeArtifactVersions],
  ["workflow templates", normalizeWorkflowTemplates],
  ["disclosure", normalizeDisclosure],
];

// ---------------------------------------------------------------------------
// Nothing throws, on anything
// ---------------------------------------------------------------------------

test("no normaliser throws on a body that is not the contract", () => {
  for (const [name, normalize] of NORMALIZERS) {
    for (const body of HOSTILE) {
      assert.doesNotThrow(
        () => normalize(body),
        `${name} threw on ${JSON.stringify(body) ?? String(body)}`,
      );
    }
  }
});

/** Walk a normalised result and assert no declared field came back undefined. */
function assertNoUndefined(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertNoUndefined(entry, `${path}[${index}]`));
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const [key, inner] of Object.entries(value)) {
    assert.notEqual(
      inner,
      undefined,
      `${path}.${key} is undefined — a component would crash reading it`,
    );
    assertNoUndefined(inner, `${path}.${key}`);
  }
}

test("an empty body yields a fully-populated shape with no undefined anywhere", () => {
  for (const [name, normalize] of NORMALIZERS) {
    assertNoUndefined(normalize({}), name);
  }
});

test("a null where a list was declared becomes an empty list", () => {
  const stages = normalizeStages({ stages: null, viewer: null });
  assert.deepEqual(stages.stages, []);
  const filing = normalizeFiling({ slots: null, attachments: null, blockers: null });
  assert.deepEqual(filing.slots, []);
  assert.deepEqual(filing.attachments, []);
  assert.deepEqual(filing.blockers, []);
  const attachments = normalizePackageAttachments({
    attachments: null,
    requirements: null,
  });
  assert.deepEqual(attachments.attachments, []);
  assert.deepEqual(attachments.requirements, []);
});

test("a list containing junk keeps only the rows that are objects", () => {
  const stages = normalizeStages({
    stages: [null, "prepare", 7, { seq: 1, stageKey: "prepare" }],
  });
  assert.equal(stages.stages.length, 1);
  assert.equal(stages.stages[0].stageKey, "prepare");
  assert.ok(Array.isArray(stages.stages[0].decisions));
  assert.ok(Array.isArray(stages.stages[0].officerTitles));
});

// ---------------------------------------------------------------------------
// FAIL CLOSED — the rules that decide what a reader is allowed to do
// ---------------------------------------------------------------------------

test("no viewer capability is ever granted by an unreadable payload", () => {
  for (const body of HOSTILE) {
    const { viewer } = normalizeStages(body);
    assert.equal(viewer.canSubmit, false);
    assert.equal(viewer.canDecide, false);
    assert.equal(viewer.canReturn, false);
    assert.equal(viewer.canFreeze, false);
  }
});

test("a capability the server did not send as literal true is false", () => {
  for (const value of ["true", 1, {}, [], "yes", null]) {
    const { viewer } = normalizeStages({ viewer: { canDecide: value } });
    assert.equal(
      viewer.canDecide,
      false,
      `a decision control was offered on ${JSON.stringify(value) ?? String(value)}`,
    );
  }
});

test("a report is never submittable, and never ready, by default", () => {
  for (const body of HOSTILE) {
    assert.equal(normalizeFiling(body).submittable, false);
    assert.equal(normalizePreflight(body).ready, false);
  }
  // Only the literal is accepted, for the same reason as the capabilities.
  assert.equal(normalizeFiling({ submittable: "true" }).submittable, false);
  assert.equal(normalizePreflight({ ready: 1 }).ready, false);
});

test("an unrecognised severity reads as blocking, never as a note", () => {
  for (const severity of [undefined, null, "", "minor", "BLOCKING", 3]) {
    const { items } = normalizePreflight({
      items: [{ code: "x", message: "m", severity }],
    });
    assert.equal(
      items[0].severity,
      "blocking",
      `a refusal was downgraded on ${String(severity)}`,
    );
  }
});

test("an unrecognised stage state reads as not reached, never as complete", () => {
  const { stages } = normalizeStages({
    stages: [{ seq: 1, state: "finished" }],
  });
  assert.equal(stages[0].state, "pending");
});

test("a decision the server did not mark as standing is drawn as superseded", () => {
  const { stages } = normalizeStages({
    stages: [{ seq: 2, decisions: [{ id: "d", decision: "approved" }] }],
  });
  assert.equal(stages[0].decisions[0].stillStands, false);
});

test("a signature slot is REQUIRED unless the server says otherwise", () => {
  const filing = normalizeFiling({ slots: [{ role: "board" }] });
  assert.equal(filing.slots[0].required, true);
  assert.equal(filing.slots[0].signedBy, null);
  assert.equal(
    normalizeFiling({ slots: [{ role: "board", required: false }] }).slots[0]
      .required,
    false,
  );
});

test("an attachment requirement is outstanding until the server says it is met", () => {
  const filing = normalizeFiling({
    attachments: [{ kind: "board_resolution", title: "Board resolution" }],
  });
  assert.equal(filing.attachments[0].satisfied, false);
  assert.equal(filing.attachments[0].applies, true);

  const list = normalizePackageAttachments({
    requirements: [{ kind: "board_resolution" }],
  });
  assert.equal(list.requirements[0].satisfied, false);
  // And an unrecognised origin reads as the regime's own requirement, which is
  // the one a signing-policy change cannot relax.
  assert.equal(list.requirements[0].origin, "family");
  assert.equal(
    normalizePackageAttachments({
      requirements: [{ kind: "x", origin: "made_up" }],
    }).requirements[0].origin,
    "family",
  );
});

test("a disclosure section is neither selected nor selectable by default", () => {
  const disclosure = normalizeDisclosure({
    sections: [{ key: "a", title: "Overview" }],
  });
  assert.equal(disclosure.sections[0].selected, false);
  assert.equal(disclosure.sections[0].selectable, false);
  // And a disclosure is not available to propose until the server says so.
  assert.equal(disclosure.available, false);
  assert.equal(normalizeDisclosure({ available: "true" }).available, false);
});

test("an unrecognised disclosure or template status is a draft, never published", () => {
  assert.equal(normalizeDisclosure({ status: "live" }).status, "draft");
  assert.equal(
    normalizeWorkflowTemplates({ templates: [{ id: "t", status: "live" }] })
      .templates[0].status,
    "draft",
  );
});

test("an artifact version is never the filed document by default", () => {
  const versions = normalizeArtifactVersions([{ id: "v", kind: "pdf" }]);
  assert.equal(versions[0].isFiled, false);
  assert.equal(versions[0].signedByName, null);
  assert.equal(versions[0].signedAt, null);
});

// ---------------------------------------------------------------------------
// Absence stays absence — nothing is invented
// ---------------------------------------------------------------------------

test("a missing report fingerprint stays null rather than becoming empty text", () => {
  assert.equal(normalizeStages({}).reviewDigest, null);
  assert.equal(normalizePreflight({}).reviewDigest, null);
  assert.equal(normalizeFiling({}).package, null);
});

test("a package that is not a real row normalises to null, not a blank package", () => {
  for (const body of [{ _package: {} }, { _package: null }, { _package: 7 }]) {
    assert.equal(normalizeFiling(body).package, null);
  }
  // Both spellings of the field are read: the generator renames `package`
  // because it is a reserved word, and a future regeneration may not.
  assert.equal(normalizeFiling({ _package: { id: "p" } })!.package!.id, "p");
  assert.equal(normalizeFiling({ package: { id: "p" } })!.package!.id, "p");
});

test("the server's own refusal sentence is carried, never replaced", () => {
  const { blockers } = normalizeFiling({
    blockers: [
      {
        code: "attachments_missing",
        severity: "blocking",
        scope: "attachment",
        message: "The board resolution has not been attached.",
      },
    ],
  });
  assert.equal(
    blockers[0].message,
    "The board resolution has not been attached.",
  );
});

test("a count the payload omits is a tally of zero, never a measurement", () => {
  const filing = normalizeFiling({ attachments: [{ kind: "k" }] });
  assert.equal(filing.attachments[0].minCount, 0);
  assert.equal(filing.attachments[0].present, 0);
  // Whereas a version number, which IS a measurement, stays absent.
  assert.equal(normalizeFiling({ _package: { id: "p" } })!.package!.version, null);
});

test("a round the payload omits stays null, so nothing is decided against round 0", () => {
  assert.equal(normalizeStages({}).round, null);
  assert.equal(normalizeStages({}).currentStageSeq, null);
});

test("a Date from the generated client becomes text, and an invalid one becomes null", () => {
  const good = normalizeFiling({
    _package: { id: "p", generatedAt: new Date("2026-03-31T00:00:00Z") },
  });
  assert.equal(good.package!.generatedAt, "2026-03-31T00:00:00.000Z");
  const bad = normalizeFiling({
    _package: { id: "p", generatedAt: new Date("not a date") },
  });
  assert.equal(bad.package!.generatedAt, null);
});

test("an unrecognised chain source reads as the regime's default", () => {
  assert.equal(normalizeStages({ source: "made_up" }).source, "framework_default");
  assert.equal(
    normalizeWorkflowTemplates({ effectiveSource: "made_up" }).effectiveSource,
    "framework_default",
  );
});

test("a blocked signature slot carries the ROLE the server named, unchanged", () => {
  const filing = normalizeFiling({
    slots: [{ role: "board", blockedBy: "approver" }],
  });
  assert.equal(filing.slots[0].blockedBy, "approver");
  assert.equal(
    normalizeFiling({ slots: [{ role: "board" }] }).slots[0].blockedBy,
    null,
  );
});

if (failures > 0) {
  console.error(`${failures} ICAAP filing normalisation test(s) failed`);
  process.exit(1);
}
console.log("ICAAP filing payload normalisation: all checks passed");
