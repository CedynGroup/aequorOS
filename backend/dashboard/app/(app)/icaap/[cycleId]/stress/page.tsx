"use client";

/**
 * Stress results and the capital plan for one ICAAP cycle.
 *
 * The page is a shell. Everything on it is a read of evidence the cycle is
 * already bound to, rendered by the platform's existing stress and capital-plan
 * components, so the ICAAP and the modules it draws on cannot state different
 * figures.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import StressCapitalPlan from "@/components/icaap/p2/StressCapitalPlan";
import { use } from "react";

export default function IcaapStressPage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && <StressCapitalPlan bankId={bank.id} cycleId={cycleId} />}
    </PageContainer>
  );
}
