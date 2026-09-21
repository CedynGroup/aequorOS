"use client";

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import AttachmentsPanel from "@/components/icaap/AttachmentsPanel";

export default function IcaapAttachmentsPage({
  params,
}: {
  params: { cycleId: string };
}) {
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <AttachmentsPanel bankId={bank.id} cycleId={params.cycleId} />}
    </PageContainer>
  );
}
