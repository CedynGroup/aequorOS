"use client";

import { useBankContext } from "@/components/shell/BankContext";
import SectionWorkspace from "@/components/icaap/SectionWorkspace";
import { use } from "react";

export default function IcaapSectionPage({
  params,
}: {
  params: Promise<{ cycleId: string; sectionKey: string }>;
}) {
  const { cycleId, sectionKey } = use(params);
  const { bank } = useBankContext();
  return (
    <div className="w-full px-8 py-6">
      {bank && (
        <SectionWorkspace
          bankId={bank.id}
          cycleId={cycleId}
          sectionKey={sectionKey}
        />
      )}
    </div>
  );
}
