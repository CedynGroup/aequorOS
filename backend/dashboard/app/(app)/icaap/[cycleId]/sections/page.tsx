"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import SectionList from "@/components/icaap/SectionList";

export default function IcaapSectionsPage({
  params,
}: {
  params: { cycleId: string };
}) {
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <SectionList bankId={bank.id} cycleId={params.cycleId} />}
    </PageContainer>
  );
}
