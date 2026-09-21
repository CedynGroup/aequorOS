"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import OverviewReadiness from "@/components/icaap/OverviewReadiness";

export default function IcaapOverviewPage({
  params,
}: {
  params: { cycleId: string };
}) {
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <OverviewReadiness bankId={bank.id} cycleId={params.cycleId} />}
    </PageContainer>
  );
}
