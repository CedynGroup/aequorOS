"use client";

import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import { useBankRules } from "@/lib/api/regulatoryRules";
import InstrumentStatusBadge from "./InstrumentStatusBadge";

/** Resolve the badge's evidence from the same register used by preparation. */
export default function RegulatoryRuleStatus({
  code,
  sdiCode,
}: {
  code: string;
  sdiCode?: string;
}) {
  const { bank } = useBankContext();
  const scope = useModuleScope();
  const rules = useBankRules(bank?.id);
  const selected = scope.institutionClass === "sdi" && sdiCode ? sdiCode : code;
  const instrument = rules.instrument(selected);
  return (
    <InstrumentStatusBadge
      status={instrument?.instrumentStatus}
      regulator={instrument?.regulator}
      effectiveFrom={instrument?.effectiveFrom}
      citation={instrument?.directiveCitation}
      sourceUrl={instrument?.sourceUrl}
      code={selected}
    />
  );
}
