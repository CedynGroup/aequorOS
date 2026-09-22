"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import AttachmentsPanel from "@/components/icaap/AttachmentsPanel";
import { use } from "react";

export default function IcaapAttachmentsPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <AttachmentsPanel bankId={bank.id} cycleId={cycleId} />}
    </PageContainer>
  );
}
