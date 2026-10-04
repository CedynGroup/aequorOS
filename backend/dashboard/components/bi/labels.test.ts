/**
 * Every token a BI surface renders has real production copy.
 *
 * Audit A11-F5 started this file with one vocabulary: `datasetRequirement` falls
 * back to titleising a `needs_data` key, so a pack naming a dataset the map
 * does not know renders a database identifier dressed up as a sentence — a
 * reader was being told "Needs data: Gl Segment Balances". The fallback is right
 * to exist (a missing label must not blank the widget) but it is not copy, and
 * nothing bound the two together, so the gap was invisible.
 *
 * Audit A360-6 found four more of the same class, and each is pinned here the
 * same way — the vocabulary is read from the SERVER's own source, never
 * restated, so a value added there fails this file on the day it lands:
 *
 *  - the `needs_data` extraction could not see a key containing a digit, so
 *    an unmapped `gl_mapping_bsd9` passed while rendering "Gl Mapping Bsd9";
 *  - the provenance drawer printed `ratio_of_sums`, `over`, `liq`, `official`;
 *  - the module names here and in the grant composer had drifted ("Internal
 *    Audit" against "Audit"), while this file promised they were the same;
 *  - the avatar menu rendered `account_admin` as "Account_admin" for every
 *    account administrator on every page.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import {
  MODULE_OPTIONS,
  ROLE_OPTIONS,
  SENSITIVITY_OPTIONS,
} from "../../lib/api/grants";
import { roleLabel } from "../../lib/api/identity";
import {
  AGGREGATION_LABELS,
  COMPONENT_ROLE_LABELS,
  ENGINE_TIER_LABELS,
  REGIME_LABELS,
  aggregationLabel,
  componentRoleLabel,
  datasetRequirement,
  engineTierLabel,
  moduleLabel,
  namedDataset,
  regimeLabel,
  sensitivityLabel,
} from "./labels";

const BACKEND_DIR = join(process.cwd(), "..");
const PACKS_DIR = join(BACKEND_DIR, "app", "domain", "bi", "packs");

/** The fallback titleises: underscores become spaces and each word is capitalised. */
function titleised(key: string): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * A vocabulary value has production copy when its map holds an EXPLICIT entry
 * that reads as words. The fallback (`labelize`) produces the titleised token,
 * and for a single-word token — "numerator", "viewer" — that is also the right
 * copy, so a string comparison cannot tell a deliberate entry from the fallback.
 * Membership can. The shape check then keeps an entry from being the wire form
 * pasted in: no underscore, and it starts like a sentence.
 */
function hasProductionCopy(
  map: Readonly<Record<string, string>>,
  token: string,
): boolean {
  const label = map[token];
  return (
    typeof label === "string" &&
    label.trim().length > 0 &&
    label !== token &&
    !/_/.test(label) &&
    /^[A-Z]/.test(label)
  );
}

// --- 1. every needs_data key a pack uses has copy --------------------------------

/**
 * The server accepts `^[a-z][a-z0-9_]*$` for a dataset key
 * (`app/schemas/bi.py`, `BiPackWidget.needs_data`). This pattern is the same
 * class, and the parity check below fails if the server's ever widens.
 */
const NEEDS_DATA_KEY = /"needs_data"\s*:\s*"([a-z][a-z0-9_]*)"/g;

function needsDataKeysIn(text: string): string[] {
  return [...text.matchAll(NEEDS_DATA_KEY)].map((match) => match[1]);
}

function needsDataKeys(): string[] {
  const keys = new Set<string>();
  for (const file of readdirSync(PACKS_DIR).filter((name) =>
    name.endsWith(".json"),
  )) {
    for (const key of needsDataKeysIn(
      readFileSync(join(PACKS_DIR, file), "utf8"),
    )) {
      keys.add(key);
    }
  }
  return [...keys].sort();
}

// The extraction must see a key with a digit in it. It did not: `[a-z_]+` stopped
// at the `9` of `gl_mapping_bsd9`, the key was never extracted, and an unmapped
// dataset one digit away from a mapped one passed this file while rendering
// "Gl Mapping Bsd9".
assert.deepEqual(
  needsDataKeysIn(
    '{"needs_data": "gl_mapping_bsd9", "needs_data":"positions"}',
  ),
  ["gl_mapping_bsd9", "positions"],
  "the needs_data extraction cannot see a key containing a digit",
);

// And the class it accepts is the server's own, read from the schema.
const schemaSource = readFileSync(
  join(BACKEND_DIR, "app", "schemas", "bi.py"),
  "utf8",
);
const serverPattern =
  /needs_data:\s*str \| None = Field\([^)]*pattern=r"([^"]+)"/.exec(
    schemaSource,
  )?.[1];
assert.equal(
  serverPattern,
  "^[a-z][a-z0-9_]*$",
  "the server's needs_data pattern changed; widen NEEDS_DATA_KEY to match it",
);

const keys = needsDataKeys();

// Anti-vacuity: if the packs moved or the field were renamed, the loop below
// would iterate nothing and this file would pass over an empty set.
if (keys.length < 3) {
  throw new Error(
    `only ${keys.length} needs_data keys found in ${PACKS_DIR}; expected several`,
  );
}
// And at least one pack key carries a digit, or the digit fix above is guarding
// nothing the packs exercise.
assert.ok(
  keys.some((key) => /[0-9]/.test(key)),
  "no pack dataset key carries a digit; the extraction's digit class is unexercised",
);

const untitled: string[] = [];
for (const key of keys) {
  const requirement = datasetRequirement(key);
  if (requirement.label === titleised(key)) {
    untitled.push(key);
  }
  if (!requirement.href) {
    untitled.push(`${key} (no href)`);
  }
}

if (untitled.length > 0) {
  throw new Error(
    `these pack datasets have no production copy, so a reader is shown the database ` +
      `identifier: ${untitled.join(", ")}. Add them to DATASET_REQUIREMENTS in labels.ts.`,
  );
}

// And prove the check can fire, on a key no pack uses.
const invented = datasetRequirement("some_unmapped_dataset");
if (
  invented.label !== "Some unmapped dataset" &&
  invented.label !== "Some Unmapped Dataset"
) {
  throw new Error(
    `the fallback no longer titleises, so the comparison above cannot detect a ` +
      `missing label: got "${invented.label}"`,
  );
}

// --- 2. no dataset is named for a widget that named none -------------------------
//
// Audit A360 H7. `namedDataset` is the one place an absent `needs_data` is
// decided, and it is decided as UNKNOWN. The previous default was "positions",
// and 26 pack widgets then blamed an empty answer on a positions upload the bank
// makes nightly, when what was missing was an official run.

assert.equal(
  namedDataset(undefined),
  null,
  "an absent key must not name a dataset",
);
assert.equal(namedDataset(null), null);
assert.equal(namedDataset(""), null, "a blank key must not name a dataset");
assert.equal(namedDataset("   "), null);
assert.deepEqual(
  namedDataset("positions"),
  datasetRequirement("positions"),
  "a named key still resolves to its dataset",
);
assert.equal(namedDataset("positions")?.href, "/data-engine/positions");

// --- 3. the provenance drawer's vocabularies -------------------------------------
//
// Each is read from the server's own source, so a new value fails here first.

function walk(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) files.push(...walk(join(dir, entry.name)));
    else if (entry.name.endsWith(".py")) files.push(join(dir, entry.name));
  }
  return files;
}

const aggregations = [
  ...new Set(
    walk(join(BACKEND_DIR, "app", "domain", "bi", "catalogue")).flatMap(
      (file) =>
        [
          ...readFileSync(file, "utf8").matchAll(/aggregation="([a-z_]+)"/g),
        ].map((match) => match[1]),
    ),
  ),
].sort();
assert.ok(
  aggregations.length >= 5,
  `only ${aggregations.length} aggregation kinds found in the catalogue source; the reader is broken`,
);
for (const aggregation of aggregations) {
  assert.ok(
    hasProductionCopy(AGGREGATION_LABELS, aggregation),
    `aggregation "${aggregation}" has no production copy: "${aggregationLabel(aggregation)}"`,
  );
}

const roleLiteral =
  /role: Literal\[((?:"[a-z_]+",?\s*)+)\]/.exec(schemaSource)?.[1] ?? "";
const componentRoles = [...roleLiteral.matchAll(/"([a-z_]+)"/g)].map(
  (match) => match[1],
);
assert.ok(
  componentRoles.length >= 3,
  "could not read BiExplainComponentRead.role from app/schemas/bi.py",
);
for (const role of componentRoles) {
  assert.ok(
    hasProductionCopy(COMPONENT_ROLE_LABELS, role),
    `component role "${role}" has no production copy: "${componentRoleLabel(role)}"`,
  );
}

// The engine-copy rules (which tier a certified figure is copied from) live
// beside the catalogue, not in it, so the whole BI domain is walked.
const tiers = [
  ...new Set(
    walk(join(BACKEND_DIR, "app", "domain", "bi")).flatMap((file) =>
      [...readFileSync(file, "utf8").matchAll(/tier="([a-z_]+)"/g)].map(
        (match) => match[1],
      ),
    ),
  ),
].sort();
assert.ok(
  tiers.length >= 2,
  "could not read the engine tiers from the catalogue source",
);
for (const tier of tiers) {
  assert.ok(
    hasProductionCopy(ENGINE_TIER_LABELS, tier),
    `engine tier "${tier}" has no production copy: "${engineTierLabel(tier)}"`,
  );
}

// The comment above `institution_types.capital_regime` names both regimes; read
// them from it, and fail loudly if the comment moved rather than checking nothing.
const regimeSource = readFileSync(
  join(BACKEND_DIR, "app", "models", "institution_type.py"),
  "utf8",
);
const regimeCodes = [
  ...regimeSource.matchAll(
    /Capital regime: '([a-z0-9]+)'[^\n]*vs '([a-z0-9]+)'/g,
  ),
].flatMap((match) => [match[1], match[2]]);
assert.equal(
  regimeCodes.length,
  2,
  "could not read the capital regime codes from app/models/institution_type.py",
);
for (const regime of regimeCodes) {
  assert.ok(
    hasProductionCopy(REGIME_LABELS, regime),
    `capital regime "${regime}" has no production copy: "${regimeLabel(regime)}"`,
  );
}
assert.equal(regimeLabel(null), null);
assert.equal(regimeLabel(""), null);

// The fallbacks still degrade to readable words, never to nothing.
assert.equal(aggregationLabel("future_kind"), "Future Kind");
assert.equal(componentRoleLabel("future_role"), "Future Role");
assert.equal(engineTierLabel("future_tier"), "Future Tier");

// --- 4. the module and sensitivity names are the grant composer's -----------------
//
// This file promised the names a BI surface shows are the SAME ones an Org Owner
// composes in Settings, and then carried its own copy of them. They drifted.

for (const [code, label] of MODULE_OPTIONS) {
  assert.equal(
    moduleLabel(code),
    label,
    `module "${code}" reads "${moduleLabel(code)}" on a BI surface and "${label}" in the grant composer`,
  );
}
for (const [code, label] of SENSITIVITY_OPTIONS) {
  assert.equal(
    sensitivityLabel(code),
    label,
    `sensitivity "${code}" reads "${sensitivityLabel(code)}" on a BI surface and "${label}" in the grant composer`,
  );
}
assert.equal(moduleLabel("future_module"), "Future Module");

// --- 5. the signed-in user's role is copy, not a token --------------------------
//
// The scalar `users.role` vocabulary (`app/core/security.py::ROLES`, plus the
// `account_admin` every `admin` was converted to by migration 202608280046).
// Read from the migration's CHECK so a new scalar role fails here first.

const migration = readFileSync(
  join(
    BACKEND_DIR,
    "alembic",
    "versions",
    "202608280046_initial_org_owner_assignment.py",
  ),
  "utf8",
);
// The migration states the CHECK twice — the legacy vocabulary it replaces and
// the one it installs — so the union of both is read, not whichever comes first.
const scalarRoles = [
  ...new Set(
    [...migration.matchAll(/"role IN \(([^)]+)\)"/g)].flatMap((check) =>
      [...check[1].matchAll(/'([a-z_]+)'/g)].map((match) => match[1]),
    ),
  ),
].sort();
assert.ok(
  scalarRoles.includes("account_admin") && scalarRoles.length >= 5,
  "could not read the users.role CHECK from migration 202608280046",
);
// A shape check here rather than map membership: every scalar role but one is a
// single English word, so its copy IS the titleised token, and what went wrong
// was the fallback's SHAPE — capitalise-the-first-letter left the underscore in.
for (const role of scalarRoles) {
  const label = roleLabel(role);
  assert.ok(
    label !== role && !/_/.test(label) && /^[A-Z]/.test(label),
    `role "${role}" has no production copy: "${label}"`,
  );
}
// The defect itself, by name: the old fallback capitalised the first letter
// only, so this read "Account_admin" under every account administrator's name.
assert.equal(/_/.test(roleLabel("account_admin")), false);
// The bundle names are the grant composer's, so one authority is never called
// two things between the avatar menu and Access → Members.
for (const [code, label] of ROLE_OPTIONS) {
  assert.equal(
    roleLabel(code),
    label,
    `role bundle "${code}" is named differently in the avatar menu`,
  );
}
assert.equal(roleLabel("account_admin"), "Organization Administrator");
assert.equal(roleLabel(undefined), "Signed in");
assert.equal(roleLabel("some_future_role"), "Some Future Role");

console.log(
  `labels.test.ts: ${keys.length} pack datasets, ${aggregations.length} aggregations, ` +
    `${componentRoles.length} component roles, ${tiers.length} engine tiers, ` +
    `${MODULE_OPTIONS.length} modules and ${scalarRoles.length} scalar roles all carry production copy.`,
);
