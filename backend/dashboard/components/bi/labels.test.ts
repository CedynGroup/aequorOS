/**
 * Every `needs_data` key a pack actually uses must have real production copy.
 *
 * Audit A11-F5. `datasetRequirement` falls back to titleising the key, so a pack
 * naming a dataset the map does not know renders a database identifier dressed up
 * as a sentence — a reader was being told "Needs data: Gl Segment Balances". The
 * fallback is right to exist (a missing label must not blank the widget) but it is
 * not copy, and nothing bound the two together, so the gap was invisible.
 *
 * The keys are read from the pack JSON rather than restated here, so a pack naming
 * a new dataset fails this test on the day it lands rather than on the day a bank
 * without that dataset opens the page.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { datasetRequirement } from "./labels";

const PACKS_DIR = join(process.cwd(), "..", "app", "domain", "bi", "packs");

function needsDataKeys(): string[] {
  const keys = new Set<string>();
  for (const file of readdirSync(PACKS_DIR).filter((name) => name.endsWith(".json"))) {
    const text = readFileSync(join(PACKS_DIR, file), "utf8");
    for (const match of text.matchAll(/"needs_data"\s*:\s*"([a-z_]+)"/g)) {
      keys.add(match[1]);
    }
  }
  return [...keys].sort();
}

const keys = needsDataKeys();

// Anti-vacuity: if the packs moved or the field were renamed, the loop below
// would iterate nothing and this file would pass over an empty set.
if (keys.length < 3) {
  throw new Error(`only ${keys.length} needs_data keys found in ${PACKS_DIR}; expected several`);
}

const untitled: string[] = [];
for (const key of keys) {
  const requirement = datasetRequirement(key);
  // The fallback titleises: underscores become spaces and each word is capitalised.
  // Copy that merely reformats the key is the defect, so compare against it.
  const titleised = key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
  if (requirement.label === titleised) {
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
if (invented.label !== "Some unmapped dataset" && invented.label !== "Some Unmapped Dataset") {
  throw new Error(
    `the fallback no longer titleises, so the comparison above cannot detect a ` +
      `missing label: got "${invented.label}"`,
  );
}

console.log(`labels.test.ts: ${keys.length} pack datasets all carry production copy.`);
