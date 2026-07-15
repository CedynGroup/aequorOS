import type { RegulatoryRunRead } from "@aequoros/risk-service-api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { isApiError, riskApi, type TenantHeaders } from "../../../lib/api";

/**
 * Bank of Ghana threshold annotations used only as display copy (threshold
 * labels and chart reference lines) when no stored run exposes the configured
 * threshold. Every measured value, status, and stored threshold shown next to
 * them comes from the API.
 */
export const bogDisplayThresholds = {
  lcrMinPct: "100",
  nsfrMinPct: "100",
  carMinPct: "10",
  carEarlyWarningPct: "10.5",
  carCriticalPct: "9",
  tier1MinPct: "8",
  cet1MinPct: "6.5",
  leverageMinPct: "3",
} as const;

export function metricThreshold(
  run: RegulatoryRunRead | undefined,
  metricCode: string,
  fallback: string,
): string {
  const result = run?.metricResults.find(
    (metric) => metric.metricCode === metricCode,
  );
  return result?.thresholdMin ?? fallback;
}

export function metricString(
  metrics: { [key: string]: unknown } | undefined,
  key: string,
): string | null {
  const value = metrics?.[key];
  if (typeof value === "string" || typeof value === "number") {
    return String(value);
  }
  return null;
}

const liquidityQueryPrefixes = [
  "alm-liquidity-dashboard",
  "alm-regulatory-runs",
  "alm-regulatory-run",
  "alm-bsd3",
];

const capitalQueryPrefixes = [
  "alm-capital-dashboard",
  "alm-regulatory-runs",
  "alm-regulatory-run",
  "alm-rwa-breakdown",
  "alm-capital-structure",
  "alm-bsd2",
];

export function useRunBaseline(
  tenant: TenantHeaders,
  bankId: string,
  periodId: string,
  module: "liquidity" | "capital",
) {
  const queryClient = useQueryClient();
  const prefixes =
    module === "liquidity" ? liquidityQueryPrefixes : capitalQueryPrefixes;
  return useMutation({
    mutationFn: () =>
      riskApi.createRegulatoryRun(tenant, bankId, {
        module,
        reportingPeriodId: periodId,
        scenarioCode: "baseline",
      }),
    onSuccess: (run) => {
      prefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
      if (run.status === "succeeded") {
        toast.success(`Baseline ${module} run succeeded`);
      } else {
        toast.error(
          run.error?.message ?? `Baseline ${module} run ${run.status}`,
        );
      }
    },
    onError: (error) => {
      toast.error(
        isApiError(error)
          ? error.message
          : `Baseline ${module} run could not be started.`,
      );
    },
  });
}
