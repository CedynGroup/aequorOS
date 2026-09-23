"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import SectionList from "@/components/icaap/SectionList";
import { use } from "react";

export default function IcaapSectionsPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <SectionList bankId={bank.id} cycleId={cycleId} />}
    </PageContainer>
  );
}
