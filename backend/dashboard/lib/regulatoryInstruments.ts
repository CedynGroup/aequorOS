import { isoDate } from "./api/values";

import type { InstrumentStatus } from "@aequoros/risk-service-api";
export type { InstrumentStatus };
export type InstrumentDate = Date | string | null | undefined;

export function instrumentDate(value: InstrumentDate): string | null {
  return typeof value === "string" ? value : value ? isoDate(value) : null;
}

/** Presentation casing for the recorded Ghana return-family issuer. */
export function instrumentIssuer(issuer: string): string {
  return issuer === "BOG" ? "BoG" : issuer;
}

/** Publication and commencement stay separate from calculation readiness. */
export function instrumentDisclosure(
  status: string | undefined,
  date?: InstrumentDate,
  regulator = "the regulator",
) {
  regulator = instrumentIssuer(regulator);
  const effective = instrumentDate(date);
  switch (status) {
    case "exposure_draft":
      return {
        label: "Draft",
        tooltip:
          `Proposed by ${regulator}, not law yet. Prepare if useful, no deadline or penalty.` +
          (effective
            ? ` Expected from ${effective} if made final.`
            : ` Start date not published by ${regulator}.`),
      };
    case "final_not_in_force":
      return {
        label: "Not yet in force",
        tooltip:
          "Final rule. " +
          (effective
            ? `Filing becomes required from ${effective}.`
            : `Start date not published by ${regulator}.`),
      };
    case "in_force":
      return {
        label: "In force",
        tooltip:
          "Required. Deadlines and penalties apply." +
          (effective
            ? ` In force from ${effective}.`
            : ` Start date not published by ${regulator}.`),
      };
    case "unpublished":
      return {
        label: "Reference",
        tooltip: `International standard used for monitoring only. No ${regulator} filing duty.`,
      };
    default:
      return {
        label: "Status unavailable",
        tooltip: "The instrument's legal status has not been established.",
      };
  }
}

export type RuleEvidence = { instrumentStatus?: InstrumentStatus };

export function isRuleInForce(instrument: RuleEvidence | undefined): boolean {
  return instrument?.instrumentStatus === "in_force";
}

export function isUpcomingRule(status: InstrumentStatus | undefined): boolean {
  return status === "exposure_draft" || status === "final_not_in_force";
}

export function preparationWarning(
  status: string | undefined,
  date?: InstrumentDate,
  regulator?: string,
): string | null {
  if (status === "in_force") return null;
  return `${instrumentDisclosure(status, date, regulator).tooltip} This pack is for preparation or monitoring. It does not establish compliance or readiness for live filing.`;
}

export function frameworkInstrumentStatus(
  status: string | undefined,
  effectiveFrom: InstrumentDate,
  asOf = isoDate(new Date()),
): InstrumentStatus | undefined {
  if (status === "exposure_draft") return "exposure_draft";
  if (status !== "final") return undefined;
  const effective = instrumentDate(effectiveFrom);
  return effective
    ? effective <= asOf
      ? "in_force"
      : "final_not_in_force"
    : undefined;
}

/** A pack keeps its sealed legal basis after the live register changes. */
export function packagePreparationWarning(
  snapshot: Record<string, unknown> | undefined,
  fallbackStatus?: string,
  fallbackDate?: InstrumentDate,
  fallbackRegulator?: string,
): string | null {
  const metadata = snapshot?.metadata;
  const icaap =
    metadata && typeof metadata === "object" && "icaap" in metadata
      ? metadata.icaap
      : undefined;
  const framework =
    icaap && typeof icaap === "object" && "framework" in icaap
      ? icaap.framework
      : undefined;
  if (framework && typeof framework === "object" && "status" in framework) {
    return preparationWarning(
      frameworkInstrumentStatus(
        typeof framework.status === "string" ? framework.status : undefined,
        "effective_from" in framework &&
          typeof framework.effective_from === "string"
          ? framework.effective_from
          : null,
      ),
      "effective_from" in framework &&
        typeof framework.effective_from === "string"
        ? framework.effective_from
        : null,
      icaap &&
        typeof icaap === "object" &&
        "regulator_short" in icaap &&
        typeof icaap.regulator_short === "string"
        ? icaap.regulator_short
        : undefined,
    );
  }
  const provenance = snapshot?.provenance;
  if (
    typeof provenance === "object" &&
    provenance !== null &&
    !Array.isArray(provenance)
  ) {
    const basis = provenance as Record<string, unknown>;
    const status = basis.instrument_status;
    if (
      status === "exposure_draft" ||
      status === "unpublished" ||
      status === "final_not_in_force" ||
      status === "in_force"
    ) {
      const date =
        typeof basis.instrument_effective_from === "string"
          ? basis.instrument_effective_from
          : null;
      return preparationWarning(
        status,
        date,
        typeof basis.regulator === "string" ? basis.regulator : undefined,
      );
    }
  }
  return snapshot
    ? preparationWarning(undefined)
    : preparationWarning(fallbackStatus, fallbackDate, fallbackRegulator);
}

export function preparationHref(href: string, preparing: boolean): string {
  return preparing
    ? `${href}${href.includes("?") ? "&" : "?"}preparing=1`
    : href;
}

export function forecastRuleApplicable(
  ruleId: string,
  liquidityInForce: boolean,
): boolean {
  return (
    liquidityInForce ||
    !["year5_lcr_above_minimum", "year5_nsfr_above_minimum"].includes(ruleId)
  );
}

/** Bank-facing calculation names; methodology ids remain in sealed evidence. */
export function readableCalculation(value: string): string {
  const key = value.startsWith("limit parameter: ")
    ? value.slice("limit parameter: ".length)
    : value.split(":")[0].trim();
  const labels: Record<string, string> = {
    lcr_pct: "Liquidity coverage ratio",
    car_pct: "Capital adequacy ratio",
    cet1_ratio_pct: "Common Equity Tier 1 ratio",
    tier1_ratio_pct: "Tier 1 capital ratio",
    leverage_ratio_pct: "Leverage ratio",
    net_own_funds_ghs: "Net own funds",
    stressed_car_end_pct: "Stressed capital adequacy ratio",
    car_erosion_pp: "Capital ratio erosion",
    car_min: "Minimum capital adequacy ratio",
    cet1_min: "Minimum Common Equity Tier 1 ratio",
    tier1_min: "Minimum Tier 1 capital ratio",
    leverage_min: "Minimum leverage ratio",
    fx_nop_single_limit_pct: "Single-currency net open position limit",
    fx_nop_aggregate_limit_pct: "Aggregate net open position limit",
    npl_limit_pct: "Non-performing loan limit",
    npl_dividend_restriction_pct:
      "Non-performing loan dividend restriction threshold",
    large_exposure_limit_pct: "Single counterparty exposure limit",
    large_exposure_id_threshold_pct: "Large exposure identification threshold",
    aggregate_large_exposure_cap: "Aggregate large exposure cap",
    irrbb_outlier_threshold_pct_tier1: "Interest rate risk outlier threshold",
    irrbb_sf_major_currency_threshold_pct:
      "Major currency significance threshold",
  };
  const ratio =
    /^(narrow|broad)_to_(volatile|short_term|total_deposits|total_assets)$/.exec(
      key,
    );
  if (ratio) {
    const denominator: Record<string, string> = {
      volatile: "volatile liabilities",
      short_term: "short-term liabilities",
      total_deposits: "total deposits",
      total_assets: "total assets",
    };
    return `${ratio[1] === "narrow" ? "Narrow" : "Broad"} liquid assets to ${denominator[ratio[2]]}`;
  }
  return labels[key] ?? "Additional governed calculation or limit";
}
