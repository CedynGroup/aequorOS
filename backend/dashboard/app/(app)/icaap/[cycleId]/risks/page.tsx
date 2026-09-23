"use client";

/**
 * Risk register and materiality matrix for one ICAAP cycle.
 *
 * The page is a shell: authority, data and every threshold belong to
 * `RiskRegister`, which reads them from the API. `bank` is the selected
 * institution from `BankContext`; when it has not resolved, nothing is
 * rendered rather than a screen keyed to the wrong tenant.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import RiskRegister from "@/components/icaap/p2/RiskRegister";
import { use } from "react";

export default function IcaapRisksPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <RiskRegister bankId={bank.id} cycleId={cycleId} />}
    </PageContainer>
  );
}
