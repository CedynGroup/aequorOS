"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import OverviewReadiness from "@/components/icaap/OverviewReadiness";
import { use } from "react";

export default function IcaapOverviewPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <OverviewReadiness bankId={bank.id} cycleId={cycleId} />}
    </PageContainer>
  );
}
