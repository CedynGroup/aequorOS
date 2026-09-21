"use client";

/**
 * Review & challenge for one ICAAP cycle.
 *
 * The chain of officers who had to look at the assessment, the independent
 * review, the challenge log, and — once the chain has approved it — the act of
 * sealing it for filing.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import { useIcaapCycle } from "@/lib/api/icaap";
import ReviewWorkspace from "@/components/icaap/p3/ReviewWorkspace";
import { use } from "react";

export default function IcaapReviewPage({
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
        <ReviewWorkspace
          bankId={bank.id}
          cycleId={cycleId}
          cycleKind={cycleQuery.data?.cycleKind ?? null}
        />
      )}
    </PageContainer>
  );
}
