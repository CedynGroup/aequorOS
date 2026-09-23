"use client";

/**
 * Filing for one ICAAP cycle: the sealed report, its checks, its signatures,
 * the documents that go with it, the record of the filing, and the files
 * themselves.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import { useIcaapCycle } from "@/lib/api/icaap";
import FilingWorkspace from "@/components/icaap/p3/FilingWorkspace";
import { use } from "react";

export default function IcaapFilingPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  const cycleQuery = useIcaapCycle(bank?.id, cycleId);

  return (
    <PageContainer className="py-6">
      {bank && (
        <FilingWorkspace
          bankId={bank.id}
          cycleId={cycleId}
          cycleKind={cycleQuery.data?.cycleKind ?? null}
          cycleTitle={cycleQuery.data?.title ?? ""}
        />
      )}
    </PageContainer>
  );
}
