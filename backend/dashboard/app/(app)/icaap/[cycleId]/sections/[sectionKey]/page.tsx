"use client";

import { useBankContext } from "@/components/shell/BankContext";
import SectionWorkspace from "@/components/icaap/SectionWorkspace";

export default function IcaapSectionPage({
  params,
}: {
  params: { cycleId: string; sectionKey: string };
}) {
  const { bank } = useBankContext();
  return (
    <div className="w-full px-8 py-6">
      {bank && (
        <SectionWorkspace
          bankId={bank.id}
          cycleId={params.cycleId}
          sectionKey={params.sectionKey}
        />
      )}
    </div>
  );
}
