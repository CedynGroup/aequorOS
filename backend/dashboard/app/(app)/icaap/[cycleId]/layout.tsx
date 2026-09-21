"use client";

/**
 * The cycle's shell: one header and one tab strip for every tab.
 *
 * Only the tabs this phase actually ships are passed to `ModuleTabs`. A
 * later-phase segment has no route file, so a typed URL is a real 404 rather
 * than an empty page implying the feature exists.
 */

import ModuleTabs from "@/components/shell/ModuleTabs";
import { useBankContext } from "@/components/shell/BankContext";
import CycleHeader from "@/components/icaap/CycleHeader";
import { icaapTabHrefs } from "@/components/icaap/tabs";
import { useIcaapCycle, useIcaapReadiness } from "@/lib/api/icaap";

export default function IcaapCycleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { cycleId: string };
}) {
  const { bank } = useBankContext();
  const cycleQuery = useIcaapCycle(bank?.id, params.cycleId);
  const readinessQuery = useIcaapReadiness(bank?.id, params.cycleId);

  return (
    <>
      {bank && cycleQuery.data && (
        <CycleHeader
          bankId={bank.id}
          cycle={cycleQuery.data}
          deadline={readinessQuery.data?.deadline}
          breadcrumbs={[
            { label: "ICAAP", href: "/icaap" },
            { label: cycleQuery.data.title },
          ]}
        />
      )}
      <ModuleTabs tabs={icaapTabHrefs(params.cycleId)} />
      {children}
    </>
  );
}
