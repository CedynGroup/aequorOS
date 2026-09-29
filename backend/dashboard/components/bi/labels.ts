"use client";

/**
 * Production names for the vocabularies the catalogue travels with.
 *
 * `module` and `sensitivity` arrive as wire codes (`cap`, `liq`, `aggregated`)
 * because they are the values the authorization evaluator decides on. A reader
 * must never see them: "liq" is not a word, and an unfamiliar code in a grant
 * sentence is the difference between an owner issuing the right binding and
 * issuing the wrong one. The names below are the SAME ones the rest of the
 * product uses for those modules, so a sentence read here matches the sentence
 * an Org Owner composes in Settings.
 *
 * An unmapped code degrades to a readable form rather than being hidden: a new
 * module must be visible in the UI the day it is added to the catalogue, even
 * before this map learns its name.
 */

// Relative on purpose: `labels.test.ts` runs this module under plain Node via
// `tsconfig.test.json`, and tsc does not rewrite the `@/` alias — a value import
// through it resolves to nothing at runtime and the suite dies before its first
// assertion. Type-only `@/` imports are fine (they are erased); a VALUE import in
// the Node-runnable set must be relative.
import { labelize } from "../../lib/api/values";
import type { BiDatasetRequirement, BiPanelKey, BiPanelSurface } from "./types";

const MODULE_LABELS: Readonly<Record<string, string>> = {
  credit: "Credit",
  risk: "Risk & Limits",
  cap: "Basel Capital",
  liq: "Liquidity Monitoring",
  irrbb: "IRRBB",
  markets: "Markets",
  fcst: "Forecasting",
  fx: "Foreign Exchange",
  ftp: "Funds Transfer Pricing",
  beh: "Behavioral Models",
  data: "Data Engine",
  reg: "Regulatory Reporting",
  account: "Account Administration",
  audit: "Internal Audit",
};

const SENSITIVITY_LABELS: Readonly<Record<string, string>> = {
  published: "Published",
  aggregated: "Aggregated",
  confidential: "Confidential",
  restricted: "Restricted",
};

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
  reconciliation_trust: {
    label: "Open the reconciliation checks",
    href: "/insights",
  },
  ingestion_quality: {
    label: "Open the ingestion history",
    href: "/data-engine",
  },
};

export function panelSurface(key: string): BiPanelSurface | null {
  return PANEL_SURFACES[key as BiPanelKey] ?? null;
}
