import assert from "node:assert/strict";
import { runBackend } from "../../../unit/backend";

import {
  MODULE_OPTIONS,
  ROLE_OPTIONS,
  SENSITIVITY_OPTIONS,
} from "../../../lib/api/grants";
import { roleLabel } from "../../../lib/api/identity";
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

const contract = runBackend<{
  datasetKeys: string[];
  aggregations: string[];
  componentRoles: string[];
  tiers: string[];
  regimes: string[];
  scalarRoles: string[];
  digitKeyAccepted: boolean;
}>(`
from app.bi.domain.catalogue import catalogue
from app.bi.domain.packs import packs
from app.bi.schemas.query import BiExplainComponentRead, BiPackWidget
from app.identity.models.user import USER_ROLES
from app.policy.service.institution_types import SEED_TYPES

cat = catalogue()
widget = BiPackWidget.model_validate({"id": "missing_data", "title": "Missing data",
    "kind": "kpi", "needs_data": "gl_mapping_bsd9"})
print(json.dumps({
    "datasetKeys": sorted({widget.needs_data for pack in packs()
        for widget in pack.widgets if widget.needs_data is not None}),
    "aggregations": sorted({measure.aggregation for measure in cat.measures()}),
    "componentRoles": BiExplainComponentRead.model_json_schema()["properties"]["role"]["enum"],
    "tiers": sorted({measure.engine_rule.tier for measure in cat.engine_measures()
        if measure.engine_rule is not None}),
    "regimes": sorted({spec.capital_regime for spec in SEED_TYPES}),
    "scalarRoles": list(USER_ROLES),
    "digitKeyAccepted": widget.needs_data == "gl_mapping_bsd9",
}))
`);

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

assert.equal(contract.digitKeyAccepted, true);
const keys = contract.datasetKeys;

// Anti-vacuity: if the packs moved or the field were renamed, the loop below
// would iterate nothing and this file would pass over an empty set.
if (keys.length < 3) {
  throw new Error(
    `only ${keys.length} needs_data keys found in the loaded packs; expected several`,
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

const aggregations = contract.aggregations;
assert.ok(
  aggregations.length >= 5,
  `only ${aggregations.length} aggregation kinds found in the loaded catalogue; the reader is broken`,
);
assert.equal(aggregationLabel("sum"), "Sum");
for (const aggregation of aggregations.filter((value) => value !== "sum")) {
  assert.ok(
    hasProductionCopy(AGGREGATION_LABELS, aggregation),
    `aggregation "${aggregation}" has no production copy: "${aggregationLabel(aggregation)}"`,
  );
}

const componentRoles = contract.componentRoles;
assert.ok(
  componentRoles.length >= 3,
  "could not read BiExplainComponentRead.role from app/bi/schemas/query.py",
);
for (const role of componentRoles) {
  assert.ok(
    hasProductionCopy(COMPONENT_ROLE_LABELS, role),
    `component role "${role}" has no production copy: "${componentRoleLabel(role)}"`,
  );
}

const tiers = contract.tiers;
assert.ok(
  tiers.length >= 2,
  "could not read the engine tiers from the loaded catalogue",
);
for (const tier of tiers) {
  assert.ok(
    hasProductionCopy(ENGINE_TIER_LABELS, tier),
    `engine tier "${tier}" has no production copy: "${engineTierLabel(tier)}"`,
  );
}

const regimeCodes = contract.regimes;
assert.ok(regimeCodes.length >= 2);
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
const scalarRoles = contract.scalarRoles;
assert.ok(
  scalarRoles.includes("account_admin") && scalarRoles.length >= 5,
  "the users.role vocabulary is incomplete",
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
