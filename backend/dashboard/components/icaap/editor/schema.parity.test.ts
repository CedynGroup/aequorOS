/**
 * The editor schema must equal the backend allowlist.
 *
 * `backend/app/domain/icaap/editor_schema.json` is the single authority for
 * what a saved ICAAP document may contain: the API validates every save
 * against it and refuses anything else. This test builds the REAL ProseMirror
 * schema from `schema.ts` and compares it, so a Tiptap upgrade that adds a
 * node, an attribute or a default mark fails here — in CI — instead of in
 * front of a preparer whose paragraph the server will not accept.
 *
 * Run: `pnpm --filter @aequoros/dashboard test`
 *
 * ZERO NORMALISATIONS. The fixture now records the same facts ProseMirror
 * does, in the forms ProseMirror uses, so every comparison below is exact:
 *
 *  - `groups` is a LIST. ProseMirror writes several groups into one
 *    space-separated string (`"block list"`), so the string is split and the
 *    two sets must be equal — not merely overlapping.
 *  - `atom` and `leaf` are SEPARATE fields, compared against `spec.atom`
 *    (absent counts as false) and `type.isLeaf`. `type.isAtom` is
 *    `atom || isLeaf` and is deliberately not used: it would let a childless
 *    node pass as a declared atom.
 *  - `doc.content` is `"block*"` on both sides. `schema.ts` replaces
 *    StarterKit's Document to get it, because the server must be able to store
 *    a section nobody has written yet.
 *
 * The fixture and the shared samples are the backend's files, read here. When
 * they disagree with the editor the fix belongs in `schema.ts`, or in the
 * backend after review — never by loosening this test.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { getSchema } from "@tiptap/core";
import {
  ICAAP_EDITOR_SCHEMA_VERSION,
  ICAAP_HEADING_LEVELS,
  icaapSchemaExtensions,
} from "./schema";

function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 10; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const ROOT = dashboardRoot();
const SCHEMA_PATH = resolve(ROOT, "../app/domain/icaap/editor_schema.json");
const SAMPLES_PATH = resolve(
  ROOT,
  "../tests/fixtures/icaap/editor_schema_samples.json",
);

type FixtureNode = {
  content: string | null;
  groups: string[];
  leaf: boolean;
  inline: boolean;
  atom: boolean;
  attrs: Record<string, unknown>;
};

type Fixture = {
  schema_version: string;
  top_node: string;
  nodes: Record<string, FixtureNode>;
  marks: Record<string, { attrs: Record<string, unknown> }>;
};

assert.ok(
  existsSync(SCHEMA_PATH),
  `the backend allowlist is missing: ${SCHEMA_PATH}. The editor cannot be ` +
    "checked against anything else, so this is a failure, not a skip.",
);
const fixture: Fixture = JSON.parse(readFileSync(SCHEMA_PATH, "utf8"));
const schema = getSchema(icaapSchemaExtensions());

function groupsOf(group: string | null | undefined): string[] {
  return (group ?? "").split(/\s+/).filter(Boolean).sort();
}

// --- node set -------------------------------------------------------------
assert.deepEqual(
  Object.keys(schema.nodes).sort(),
  Object.keys(fixture.nodes).sort(),
  "the editor's node set differs from the backend allowlist: a document the " +
    "editor can build would be refused on save (or a node the server accepts " +
    "cannot be written). Disable the extension, or change the allowlist on " +
    "the backend — never only here.",
);

assert.equal(schema.topNodeType.name, fixture.top_node);
assert.equal(ICAAP_EDITOR_SCHEMA_VERSION, fixture.schema_version);

// --- per node -------------------------------------------------------------
for (const [name, expected] of Object.entries(fixture.nodes)) {
  const type = schema.nodes[name];
  assert.ok(type, `node ${name} is missing from the editor schema`);
  const spec = type.spec as { content?: string; group?: string; atom?: boolean };

  assert.equal(
    spec.content ?? null,
    expected.content,
    `node ${name}: content expression differs (editor ${JSON.stringify(
      spec.content ?? null,
    )}, backend ${JSON.stringify(expected.content)})`,
  );
  assert.deepEqual(
    groupsOf(spec.group),
    [...expected.groups].sort(),
    `node ${name}: groups differ`,
  );
  assert.equal(type.isInline, expected.inline, `node ${name}: inline differs`);
  assert.equal(type.isLeaf, expected.leaf, `node ${name}: leaf differs`);
  assert.equal(
    Boolean(spec.atom),
    expected.atom,
    `node ${name}: declared atomicity differs`,
  );

  const actualAttrs = Object.keys(type.spec.attrs ?? {}).sort();
  const expectedAttrs = Object.keys(expected.attrs ?? {}).sort();
  assert.deepEqual(
    actualAttrs,
    expectedAttrs,
    `node ${name}: attribute names differ (editor ${JSON.stringify(
      actualAttrs,
    )}, backend ${JSON.stringify(expectedAttrs)}). Override the attribute in ` +
      "schema.ts rather than editing the backend fixture.",
  );
}

// --- marks ----------------------------------------------------------------
assert.deepEqual(
  Object.keys(schema.marks).sort(),
  Object.keys(fixture.marks).sort(),
  "the editor's mark set differs from the backend allowlist (StarterKit " +
    "enables Code, Strike and Link by default — they must stay disabled).",
);
for (const name of Object.keys(fixture.marks)) {
  assert.deepEqual(
    Object.keys(schema.marks[name].spec.attrs ?? {}),
    Object.keys(fixture.marks[name].attrs ?? {}),
    `mark ${name}: attributes differ; the server rejects a mark with attrs`,
  );
}

// --- heading levels -------------------------------------------------------
const headingLevels = (fixture.nodes.heading?.attrs?.level ?? {}) as {
  enum?: number[];
};
assert.deepEqual(
  [...ICAAP_HEADING_LEVELS],
  headingLevels.enum,
  "the toolbar offers heading levels the server does not accept",
);

// --- D-028 ----------------------------------------------------------------
assert.ok(
  Object.keys(schema.nodes.paragraph.spec.attrs ?? {}).includes(
    "aiSuggestionId",
  ),
  "D-028: paragraphs must carry a nullable aiSuggestionId from P1, so the AI " +
    "phase needs no migration of stored documents",
);
assert.equal(
  schema.nodes.paragraph.spec.attrs?.aiSuggestionId?.default ?? null,
  null,
  "D-028: aiSuggestionId defaults to null — nothing a human writes is a suggestion",
);

// --- shared samples -------------------------------------------------------
/**
 * The fixture separates three populations, and each is treated differently on
 * purpose. The selector is the fixture's OWN `structural` flag, not a list of
 * codes kept here — so a sample A reclassifies changes what this test asserts
 * without an edit on this side.
 *
 *  - `valid`           — the grammar must accept every one, as a `doc`.
 *  - `invalid`         — defects the server refuses. Those flagged
 *                        `structural: true` are the ones the BROWSER's grammar
 *                        can see as well; the rest (length, control
 *                        characters, attribute ranges, unknown keys) are the
 *                        service's, and asserting them here would overstate
 *                        what the editor guarantees.
 *  - `service_invalid` — documents the grammar ACCEPTS and only the service
 *                        rejects. They must parse cleanly here; that is the
 *                        whole point of the category.
 *
 * ONE NAMED EXCEPTION, `heading_level_one`. The fixture marks it structural,
 * but Tiptap v3 builds a node's ProseMirror attribute spec from
 * `addAttributes` and does not forward ProseMirror's `validate` hook (verified
 * against 3.31.3 through both `addGlobalAttributes` and `extendNodeSchema`),
 * so no schema-level enum on `heading.level` is reachable. In practice the
 * editor cannot PRODUCE a level 1 — the toolbar offers 2–4 and StarterKit's
 * `levels` governs paste parsing — and the server refuses one outright; only a
 * hand-written document could carry it. The exception is asserted to still be
 * necessary, so it cannot outlive its cause.
 */
const GRAMMAR_CANNOT_ENFORCE = new Set(["heading_level_one"]);

type Sample = {
  doc: unknown;
  code?: string;
  name?: string;
  structural?: boolean;
  rejected_by?: string;
};
type Samples = {
  valid?: Sample[];
  invalid?: Sample[];
  service_invalid?: Sample[];
};

assert.ok(
  existsSync(SAMPLES_PATH),
  `the shared document samples are missing: ${SAMPLES_PATH}`,
);
const samples: Samples = JSON.parse(readFileSync(SAMPLES_PATH, "utf8"));

/** Parse as the TOP node. `nodeFromJSON` alone would happily build a paragraph. */
function parseDocument(doc: unknown): void {
  const node = schema.nodeFromJSON(doc);
  if (node.type !== schema.topNodeType) {
    throw new Error(`document's top node is ${node.type.name}, not a doc`);
  }
  node.check();
}

let accepted = 0;
let rejected = 0;

for (const sample of [
  ...(samples.valid ?? []),
  ...(samples.service_invalid ?? []),
]) {
  const label = sample.name ?? sample.code ?? "(unnamed)";
  try {
    parseDocument(sample.doc);
  } catch (error) {
    throw new Error(
      `sample "${label}" must be grammar-valid but the editor schema refused ` +
        `it: ${String(error)}`,
    );
  }
  accepted += 1;
}

const structural = (samples.invalid ?? []).filter(
  (sample) => sample.structural === true,
);
for (const sample of structural) {
  const label = sample.name ?? sample.code ?? "(unnamed)";
  if (GRAMMAR_CANNOT_ENFORCE.has(label)) {
    assert.doesNotThrow(
      () => parseDocument(sample.doc),
      `"${label}" is now rejected by the editor grammar, so its recorded ` +
        "exception in GRAMMAR_CANNOT_ENFORCE is stale — delete the entry.",
    );
    continue;
  }
  assert.throws(
    () => parseDocument(sample.doc),
    `structural sample "${label}" was accepted by the editor schema; the ` +
      "browser would let an author build a document the server refuses",
  );
  rejected += 1;
}

assert.ok(
  accepted >= (samples.valid ?? []).length &&
    rejected >= structural.length - GRAMMAR_CANNOT_ENFORCE.size,
  `the shared samples must exercise both directions; accepted ${accepted}, rejected ${rejected}`,
);

console.log(
  `schema.parity.test.ts: editor schema matches the backend allowlist exactly; ` +
    `${accepted} document(s) accepted, ${rejected} structurally rejected, ${GRAMMAR_CANNOT_ENFORCE.size} left to the service.`,
);
