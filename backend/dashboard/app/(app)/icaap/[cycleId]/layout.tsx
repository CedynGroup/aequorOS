"use client";

import { useSearchParams } from "next/navigation";
import { preparationHref } from "@/lib/regulatoryInstruments";

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
import { use, Suspense, type ReactNode } from "react";

type Props = { children: ReactNode; params: Promise<{ cycleId: string }> };

export default function IcaapCycleLayout(props: Props) {
  return (
    <Suspense>
      <IcaapCycleShell {...props} />
    </Suspense>
  );
}

function IcaapCycleShell({ children, params }: Props) {
  const preparing = useSearchParams().get("preparing") === "1";
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  const cycleQuery = useIcaapCycle(bank?.id, cycleId);
  const readinessQuery = useIcaapReadiness(bank?.id, cycleId);

  return (
    <>
      {bank && cycleQuery.data && (
        <CycleHeader
          bankId={bank.id}
          cycle={cycleQuery.data}
          deadline={readinessQuery.data?.deadline}
          breadcrumbs={[
            { label: "ICAAP", href: preparationHref("/icaap", preparing) },
            { label: cycleQuery.data.title },
          ]}
        />
      )}
      <ModuleTabs tabs={icaapTabHrefs(cycleId, preparing)} />
      {children}
    </>
  );
}
