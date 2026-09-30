"use client";

/**
 * Production names for the vocabularies the catalogue travels with.
 *
 * `module` and `sensitivity` arrive as wire codes (`cap`, `liq`, `aggregated`)
 * because they are the values the authorization evaluator decides on. A reader
 * must never see them: "liq" is not a word, and an unfamiliar code in a grant
 * sentence is the difference between an owner issuing the right binding and
 * issuing the wrong one. The names are the SAME ones the rest of the product
 * uses for those modules — they are READ from the grant composer's own option
 * lists in `lib/api/grants.ts` rather than restated here, so a sentence read on
 * a BI surface cannot word a module differently from the sentence an Org Owner
 * composes in Settings. (They did: this file said "Internal Audit" — the name of
 * persona #13 in `docs/rbac.md` — for a module the composer and the RBAC module
 * list both call "Audit".)
 *
 * An unmapped code degrades to a readable form rather than being hidden: a new
 * module must be visible in the UI the day it is added to the catalogue, even
 * before this map learns its name.
 *
 * The same rule covers every other token the provenance drawer shows — an
 * aggregation, a component's role, an engine tier, a capital regime. Each is a
 * closed server vocabulary; each has its copy here; `labels.test.ts` reads the
 * server's own source for the vocabularies and fails when one of them gains a
 * value this file has not named.
 */

// Relative on purpose: `labels.test.ts` runs this module under plain Node via
// `tsconfig.test.json`, and tsc does not rewrite the `@/` alias — a value import
// through it resolves to nothing at runtime and the suite dies before its first
// assertion. Type-only `@/` imports are fine (they are erased); a VALUE import in
// the Node-runnable set must be relative.
import { MODULE_OPTIONS, SENSITIVITY_OPTIONS } from "../../lib/api/grants";
import { labelize } from "../../lib/api/values";
import type { BiDatasetRequirement, BiPanelKey, BiPanelSurface } from "./types";

function fromOptions(
  options: readonly (readonly [string, string])[],
): Readonly<Record<string, string>> {
  return Object.fromEntries(options.map(([code, label]) => [code, label]));
}

/** One source of module names: the grant composer's own list. */
const MODULE_LABELS: Readonly<Record<string, string>> =
  fromOptions(MODULE_OPTIONS);

const SENSITIVITY_LABELS: Readonly<Record<string, string>> =
  fromOptions(SENSITIVITY_OPTIONS);

export function moduleLabel(code: string): string {
  return MODULE_LABELS[code] ?? labelize(code);
}

export function sensitivityLabel(code: string): string {
  return SENSITIVITY_LABELS[code] ?? labelize(code);
}

/** The grant sentence an Org Owner would issue for one catalogue member. */
export function grantSentence(module: string, sensitivity: string): string {
  return `${moduleLabel(module)} · ${sensitivityLabel(sensitivity)} · View`;
}

/**
 * How a measure's designation reads on screen. `filed` figures are the only
 * ones that may be shown as certified; everything else is named for what it is.
 */
export function designationLabel(designation: string | null): string | null {
  if (!designation) return null;
  if (designation === "filed") return "Filed figure";
  if (designation === "advisory_only") return "Advisory only";
  if (designation === "supervisory_monitoring") return "Supervisory monitoring";
  if (designation === "unregistered") return "Not a registered return figure";
  return labelize(designation);
}

/**
 * How a measure is computed, in words (`BiMeasureRead.aggregation`).
 *
 * The vocabulary is the catalogue's (`app/domain/bi/catalogue/*.py`); the
 * drawer printed the token itself — "Aggregation ratio_of_sums" — and a
 * reviewer opening Explain on the net interest margin was handed an identifier
 * where the one sentence that explains the figure belongs.
 */
export const AGGREGATION_LABELS: Readonly<Record<string, string>> = {
  count: "Count of records",
  flow_sum: "Sum of the movements in the window",
  hhi: "Herfindahl-Hirschman concentration index",
  last_value: "Latest value in the window",
  ratio_of_sums: "Ratio of two sums",
  share: "Share of the total",
  top_n_share: "Share held by the largest groups",
  weighted_avg: "Weighted average",
};

export function aggregationLabel(code: string): string {
  return AGGREGATION_LABELS[code] ?? labelize(code);
}

/**
 * The part a component measure plays in a composed figure
 * (`BiExplainComponentRead.role`). `over` is the dimension a share is taken
 * across, which "over" alone does not say.
 */
export const COMPONENT_ROLE_LABELS: Readonly<Record<string, string>> = {
  numerator: "Numerator",
  denominator: "Denominator",
  weight: "Weighted by",
  over: "Share taken across",
};

export function componentRoleLabel(role: string): string {
  return COMPONENT_ROLE_LABELS[role] ?? labelize(role);
}

/**
 * Which engine tier a certified figure was copied from
 * (`BiExplainEngineRead.tier`; ARCHITECTURE.md §3b).
 */
export const ENGINE_TIER_LABELS: Readonly<Record<string, string>> = {
  live: "Live tier — recomputed as data arrives",
  official: "Official tier — a sealed filing run",
};

export function engineTierLabel(tier: string): string {
  return ENGINE_TIER_LABELS[tier] ?? labelize(tier);
}

/**
 * The capital regime an engine row was computed under
 * (`BiExplainEngineRead.regime`, from `institution_types.capital_regime`).
 * Named by the institution class it applies to, not by the jurisdiction's
 * instrument: display code carries no regulator or statute literal.
 */
export const REGIME_LABELS: Readonly<Record<string, string>> = {
  crd: "Universal bank capital regime",
  s29: "Specialised deposit-taker capital regime",
};

export function regimeLabel(regime: string | null | undefined): string | null {
  if (!regime) return null;
  return REGIME_LABELS[regime] ?? labelize(regime);
}

/**
 * A Data Engine dataset key, as the bank's own upload screens name it, with the
 * route that accepts it.
 *
 * The key is a DATASET NAME the pack file carries
 * (`BiPackWidget.needs_data`), not a label — so the words a reader sees are
 * decided here, once, and match the names the ingestion surfaces use. An
 * unmapped key degrades to a readable form and the generic upload route rather
 * than being hidden: a dataset added to a pack must be nameable on screen the
 * day it is added, even before this map learns its wording.
 */
const DATASET_REQUIREMENTS: Readonly<Record<string, BiDatasetRequirement>> = {
  positions: {
    label: "Positions and balances for this date",
    href: "/data-engine/positions",
  },
  business_units: {
    label: "The branch and region register",
    href: "/data-engine/excel-csv",
  },
  performance_targets: {
    label: "Performance targets",
    href: "/data-engine/excel-csv",
  },
  gl_mapping_bsd7: {
    label: "The general ledger account mapping",
    href: "/data-engine/excel-csv",
  },
  // Phase 5. Without an entry the fallback titleises the key, and a reader was
  // being told "Needs data: Gl Segment Balances" — a database identifier dressed
  // up, which is exactly what this map exists to prevent. What the bank actually
  // has to send is its ledger broken down by branch.
  gl_segment_balances: {
    label: "The general ledger broken down by branch",
    href: "/data-engine/excel-csv",
  },
};

const GENERIC_UPLOAD_HREF = "/data-engine/excel-csv";

export function datasetRequirement(key: string): BiDatasetRequirement {
  return (
    DATASET_REQUIREMENTS[key] ?? {
      label: labelize(key),
      href: GENERIC_UPLOAD_HREF,
    }
  );
}

/**
 * The dataset a widget NAMED, or null when it named none.
 *
 * This is the one place an absent `needs_data` is decided, and it is decided as
 * "unknown" rather than as a default. It used to default to `positions`, and
 * every query widget whose pack file names no dataset — the Board pack's capital
 * adequacy, liquidity coverage and net interest margin among 26 others — then
 * told a reader, on a date with no minted official run, that the institution
 * had not uploaded its positions. Positions are pushed nightly; what was missing
 * was the run. A widget that does not know why it is empty must say it does not
 * know, and `NeedsDataWidget` renders exactly that for `null`.
 */
export function namedDataset(
  key: string | null | undefined,
): BiDatasetRequirement | null {
  if (typeof key !== "string" || key.trim().length === 0) return null;
  return datasetRequirement(key);
}

/**
 * Where a surface a certified pack EMBEDS actually lives.
 *
 * A pack names a closed set of platform surfaces and can express no route, no
 * parameter and no query of its own; this map is the one place a key becomes a
 * link, and the surface it points at authorizes whoever follows it. An unmapped
 * key is `null`, and the widget then states the surface it is waiting on rather
 * than offering a link to nowhere.
 */
const PANEL_SURFACES: Readonly<Record<BiPanelKey, BiPanelSurface>> = {
  credit_migration: {
    label: "Open delinquency and migration",
    href: "/credit/delinquency",
  },
  credit_vintages: {
    label: "Open the vintage cohorts",
    href: "/credit/vintages",
  },
  return_calendar: {
    label: "Open the filing calendar",
    href: "/submissions/calendar",
  },
  attestation_status: {
    label: "Open the signature register",
    href: "/submissions/signatures",
  },
  ingestion_quality: {
    label: "Open the ingestion history",
    href: "/data-engine",
  },
};

export function panelSurface(key: string): BiPanelSurface | null {
  return PANEL_SURFACES[key as BiPanelKey] ?? null;
}
