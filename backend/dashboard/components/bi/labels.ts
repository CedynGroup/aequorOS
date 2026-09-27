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

import { labelize } from "@/lib/api/values";

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
