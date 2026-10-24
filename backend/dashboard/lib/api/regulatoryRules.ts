"use client";

import { useModuleScope } from "@/components/shell/BankContext";
import { isRuleInForce } from "@/lib/regulatoryInstruments";
import { useBankAlerts, useReturnTemplates } from "./hooks";

export function useBankRules(bankId: string | undefined) {
  const scope = useModuleScope();
  const query = useReturnTemplates(bankId);
  const instrument = (code: string | undefined, sdiCode?: string) =>
    query.data?.templates.find(
      (item) =>
        item.code ===
        (scope.institutionClass === "sdi" && sdiCode ? sdiCode : code),
    );
  const isInForce = (code: string, sdiCode?: string) =>
    Boolean(bankId) && isRuleInForce(instrument(code, sdiCode));
  const isModuleInForce = (module: string) =>
    module === "liquidity"
      ? scope.institutionClass !== "sdi" && isInForce("LCR-NSFR")
      : module === "irr"
        ? isInForce("IRRBB-PILOT", "SDI-IRRBB-QUARTERLY")
        : true;
  return { query, instrument, isInForce, isModuleInForce };
}

export function useInForceAlerts(bankId: string | undefined, limit = 200) {
  const query = useBankAlerts(bankId, limit);
  const { isModuleInForce } = useBankRules(bankId);
  const data = query.data;
  if (!data) return query;
  const items = data.items.filter((item) => isModuleInForce(item.module));
  const bySeverity: Record<string, number> = {};
  const byModule: Record<string, number> = {};
  for (const item of items) {
    bySeverity[item.severity] = (bySeverity[item.severity] ?? 0) + 1;
    byModule[item.module] = (byModule[item.module] ?? 0) + 1;
  }
  return {
    ...query,
    data: { ...data, items, total: items.length, bySeverity, byModule },
  };
}
