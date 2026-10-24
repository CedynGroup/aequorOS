import { regShort } from "./format";
import { isoDate } from "./api/values";

import type { InstrumentStatus } from "@aequoros/risk-service-api";
export type { InstrumentStatus };
export type InstrumentDate = Date | string | null | undefined;

export function instrumentDate(value: InstrumentDate): string | null {
  return typeof value === "string" ? value : value ? isoDate(value) : null;
}

/** Publication and commencement stay separate from calculation readiness. */
export function instrumentDisclosure(
  status: string | undefined,
  date?: InstrumentDate,
) {
  const effective = instrumentDate(date);
  switch (status) {
    case "exposure_draft":
      return {
        label: "Draft",
        tooltip:
          `Proposed by ${regShort()}, not law yet. Prepare if useful, no deadline or penalty.` +
          (effective
            ? ` Expected from ${effective} if made final.`
            : " Expected start date is not established."),
      };
    case "final_not_in_force":
      return {
        label: "Not yet in force",
        tooltip:
          "Final rule. " +
          (effective
            ? `Filing becomes required from ${effective}.`
            : "The commencement date is not established."),
      };
    case "in_force":
      return {
        label: "In force",
        tooltip:
          "Required. Deadlines and penalties apply." +
          (effective
            ? ` In force from ${effective}.`
            : " The effective date is not established in the register."),
      };
    case "unpublished":
      return {
        label: "Reference",
        tooltip: `International standard used for monitoring only. No ${regShort()} filing duty.`,
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
): string | null {
  if (status === "in_force") return null;
  return `${instrumentDisclosure(status, date).tooltip} This pack is for preparation or monitoring. It does not establish compliance or readiness for live filing.`;
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
      return preparationWarning(status, date);
    }
  }
  return snapshot
    ? preparationWarning(undefined)
    : preparationWarning(fallbackStatus, fallbackDate);
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
