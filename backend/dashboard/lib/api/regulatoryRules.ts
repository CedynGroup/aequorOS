"use client";

import { useModuleScope } from "@/components/shell/BankContext";
import {
  isRuleInForce,
  forecastRuleApplicable,
  forecastStatus,
} from "@/lib/regulatoryInstruments";
import { useBankAlerts, useReturnTemplates, useLiveSummary } from "./hooks";

export function useBankRules(bankId: string | undefined) {
  const scope = useModuleScope();
  const query = useReturnTemplates(bankId);
  const instrument = (code: string | undefined, sdiCode?: string) =>
    query.data?.templates.find(
      (item) =>
        item.code ===
        (scope.institutionClass === "sdi" && sdiCode ? sdiCode : code),
    );
  const requiredCodes =
    scope.institutionClass === "sdi"
      ? ["SDI-LMT-MONTHLY", "SDI-IRRBB-QUARTERLY"]
      : ["LCR-NSFR", "IRRBB-PILOT"];
  const error =
    query.error ??
    (query.data &&
    requiredCodes.some((code) => !instrument(code)?.instrumentStatus)
      ? new Error("Regulatory rule status is unavailable.")
      : null);
  const isLoading = Boolean(bankId) && !query.data && !error;
  const isReady = Boolean(bankId && query.data) && !error;
  const isInForce = (code: string, sdiCode?: string) => {
    const evidence = instrument(code, sdiCode);
    return bankId && !query.error && evidence?.instrumentStatus
      ? isRuleInForce(evidence)
      : undefined;
  };
  const isModuleInForce = (module: string) =>
    module === "liquidity"
      ? scope.institutionClass !== "sdi" && isInForce("LCR-NSFR")
      : module === "irr"
        ? isInForce("IRRBB-PILOT", "SDI-IRRBB-QUARTERLY")
        : true;
  return {
    query,
    instrument,
    isInForce,
    isModuleInForce,
    isLoading,
    error,
    isReady,
  };
}

export function useInForceAlerts(bankId: string | undefined, limit = 200) {
  const query = useBankAlerts(bankId, limit);
  const rules = useBankRules(bankId);
  const data = rules.isReady && !query.error ? query.data : undefined;
  const items =
    data?.items.filter(
      (item) =>
        rules.isModuleInForce(item.module) &&
        (item.module !== "forecast" ||
          forecastRuleApplicable(
            item.ruleId,
            rules.isModuleInForce("liquidity") === true,
          )),
    ) ?? [];
  const bySeverity: Record<string, number> = {};
  const byModule: Record<string, number> = {};
  for (const item of items) {
    bySeverity[item.severity] = (bySeverity[item.severity] ?? 0) + 1;
    byModule[item.module] = (byModule[item.module] ?? 0) + 1;
  }
  const error = query.error ?? rules.error;
  return {
    data: data && { ...data, items, total: items.length, bySeverity, byModule },
    error,
    isLoading: !error && (query.isLoading || rules.isLoading),
    refetch: () => Promise.all([query.refetch(), rules.query.refetch()]),
  };
}

export function useInForceLiveSummary(bankId: string | undefined) {
  const query = useLiveSummary(bankId);
  const rules = useBankRules(bankId);
  const error = query.error ?? rules.error;
  return {
    refetch: () => Promise.all([query.refetch(), rules.query.refetch()]),
    error,
    isLoading: !error && (query.isLoading || rules.isLoading),
    data:
      rules.isReady && !query.error && query.data
        ? {
            ...query.data,
            modules: query.data.modules
              .filter((view) => rules.isModuleInForce(view.module))
              .map((view) =>
                view.module === "forecast"
                  ? {
                      ...view,
                      status: forecastStatus(
                        view.metrics,
                        rules.isModuleInForce("liquidity") === true,
                      ),
                    }
                  : view,
              ),
          }
        : undefined,
  };
}
