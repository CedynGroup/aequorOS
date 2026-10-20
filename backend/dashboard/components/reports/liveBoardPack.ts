import type { LiveModule, LiveModuleView } from "@aequoros/risk-service-api";
import {
  livePrimaryMetricKey,
  type LivePrimaryMetric,
} from "@/components/live/moduleDisplay";
import { formatFigure, labelize, numOrNull } from "@/lib/api/values";
import { fmtNum, fmtPct } from "@/lib/format";
import type { MetricRow } from "./BoardPackSections";

/** Live generations are baseline materialisations, never registry-selected runs. */
export function liveEvidenceProblem(
  view: LiveModuleView | undefined,
): string | null {
  if (!view)
    return "Missing live generation — no figures or evidence available.";
  if (view.pipelineState !== "ready") {
    return `${labelize(view.pipelineState)} live generation — figures withheld. Repair the inputs and refresh before review.`;
  }
  if (view.metrics.availability === "unavailable") {
    return "Live analysis unavailable — figures withheld.";
  }
  if (
    !view.computedFromInputHash ||
    !view.engineVersion ||
    !view.calculationGeneration ||
    !Number.isFinite(view.computedAt?.getTime()) ||
    !Number.isFinite(view.sourceAsOfDate?.getTime())
  ) {
    return "Incomplete live-generation evidence — figures withheld.";
  }
  return null;
}

/** An absent or malformed headline never becomes a measured zero. */
export function liveBoardHeadline(
  view: LiveModuleView,
): LivePrimaryMetric | null {
  const raw: unknown = view.metrics[livePrimaryMetricKey(view.module)];
  const value = numOrNull(
    typeof raw === "string" ? raw.trim() : typeof raw === "number" ? raw : null,
  );
  if (value === null) return null;
  const label =
    METRICS[view.module]?.find(
      ([key]) => key === livePrimaryMetricKey(view.module),
    )?.[1] ?? "Headline metric";
  return { label, value: fmtPct(value, 2) };
}

type MetricSpec = readonly [
  key: string,
  label: string,
  unit: "pct" | "amount" | "number" | "text",
];
const METRICS: Partial<Record<LiveModule, readonly MetricSpec[]>> = {
  liquidity: [
    ["lcr_pct", "Liquidity Coverage Ratio", "pct"],
    ["nsfr_pct", "Net Stable Funding Ratio", "pct"],
    ["hqla_total_ghs", "High-quality liquid assets", "amount"],
    ["net_outflows_30d_ghs", "Net outflows (30 days)", "amount"],
  ],
  capital: [
    ["car_pct", "Capital Adequacy Ratio", "pct"],
    ["cet1_ratio_pct", "CET1 ratio", "pct"],
    ["tier1_ratio_pct", "Tier 1 ratio", "pct"],
    ["leverage_ratio_pct", "Leverage ratio", "pct"],
    ["total_rwa_ghs", "Total risk-weighted assets", "amount"],
    ["total_capital_ghs", "Total regulatory capital", "amount"],
    ["net_own_funds_ghs", "Net own funds", "amount"],
  ],
  credit: [
    ["npl_ratio_pct", "NPL ratio", "pct"],
    ["npl_exposure_ghs", "Non-performing exposure", "amount"],
    ["provision_coverage_pct", "Provision coverage", "pct"],
    ["gross_loans_ghs", "Gross loan book", "amount"],
    ["total_provision_required_ghs", "Provisions required", "amount"],
  ],
  irr: [
    ["worst_eve_change_pct_tier1", "Worst ΔEVE / Tier 1", "pct"],
    [
      "worst_scenario",
      "Worst IRRBB shock within the baseline analysis",
      "text",
    ],
    ["duration_gap", "Duration gap (years)", "number"],
    ["nii_base_ghs", "NII (base, 12m)", "amount"],
  ],
  fx: [
    ["nop_pct_tier1", "Net open position / Tier 1", "pct"],
    ["nop_ghs", "Net open position", "amount"],
    ["single_ccy_max_pct", "Largest single currency / Tier 1", "pct"],
    ["single_ccy_max_currency", "Largest single currency", "text"],
    ["var_99_1d_ghs", "Standalone VaR (99%, 1 day)", "amount"],
  ],
  rating: [["pit_pd_upper_pct", "PIT PD upper band", "pct"]],
  forecast: [["year5_car_pct", "Year-5 CAR", "pct"]],
  ftp: [
    ["portfolio_nim_pct", "Portfolio NIM", "pct"],
    ["nmd_core_pct", "Core NMD share", "pct"],
    ["products_below_min_margin", "Products below minimum margin", "number"],
    ["total_balance_ghs", "Total funded balance", "amount"],
  ],
};

/** Only figures actually published in this generation enter the table. */
export function liveBoardRows(
  view: LiveModuleView,
  currency: string,
): MetricRow[] {
  const rows = (METRICS[view.module] ?? [])
    .filter(([key]) => key in view.metrics)
    .map(([key, label, unit]) => {
      const raw: unknown = view.metrics[key];
      const numeric =
        typeof raw === "string"
          ? raw.trim()
          : typeof raw === "number"
            ? raw
            : null;
      return {
        label,
        value:
          unit === "text"
            ? typeof raw === "string" && raw
              ? labelize(raw)
              : "Unavailable"
            : formatFigure(numeric, (value) =>
                unit === "pct"
                  ? fmtPct(value, 2)
                  : unit === "amount"
                    ? `${currency} ${fmtNum(value, 2)}`
                    : fmtNum(value, 2),
              ),
      };
    });
  return rows.length ? rows : [{ label: "Live metrics", value: "Unavailable" }];
}

/** Printed evidence timestamps are unambiguous across reviewer time zones. */
export function boardTimestamp(value: Date | string): string {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toISOString().replace("T", " ").replace(".000Z", " UTC")
    : "Unavailable";
}
