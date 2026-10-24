"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { preparationHref } from "@/lib/regulatoryInstruments";

/**
 * The ICAAP workspace: the institution's assessment cycles.
 *
 * Visibility is decided in `lib/modules.ts`, not here — the route is hidden
 * (and 404s) without the deployment flag, without CAP/confidential view, and
 * for an SDI tenant, which has no Pillar 2 regime.
 */

import { useRouter } from "next/navigation";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { useBankContext } from "@/components/shell/BankContext";
import RegulatoryModuleTabs from "@/components/shell/RegulatoryModuleTabs";
import CycleList from "@/components/icaap/CycleList";
import { regShort } from "@/lib/format";

export default function IcaapPage() {
  return (
    <Suspense>
      <IcaapWorkspace />
    </Suspense>
  );
}

function IcaapWorkspace() {
  const preparing = useSearchParams().get("preparing") === "1";
  const { bank } = useBankContext();
  const router = useRouter();

  return (
    <>
      <PageHeader
        eyebrow="ICAAP"
        title="Internal Capital Adequacy Assessment"
        subtitle={`The institution's own assessment of the capital it needs, and the evidence behind it, as filed with ${regShort()}.`}
      />
      <RegulatoryModuleTabs tabs={[]} contextRule="ICAAP-REPORT">
        <PageContainer className="py-6">
          {bank && (
            <CycleList
              bankId={bank.id}
              onCreated={(cycleId) =>
                router.push(
                  preparationHref(`/icaap/${cycleId}/overview`, preparing),
                )
              }
            />
          )}
        </PageContainer>
      </RegulatoryModuleTabs>
    </>
  );
}
