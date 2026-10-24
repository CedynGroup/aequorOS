"use client";

import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import { useReturnTemplates } from "@/lib/api/hooks";
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
  const query = useReturnTemplates(bank?.id);
  const selected = scope.institutionClass === "sdi" && sdiCode ? sdiCode : code;
  const instrument = query.data?.templates.find(
    (item) => item.code === selected,
  );
  return (
    <InstrumentStatusBadge
      status={instrument?.instrumentStatus}
      effectiveFrom={instrument?.effectiveFrom}
      citation={instrument?.directiveCitation}
      sourceUrl={instrument?.sourceUrl}
      code={selected}
    />
  );
}
