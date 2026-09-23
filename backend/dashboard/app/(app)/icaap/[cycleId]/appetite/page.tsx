"use client";

/**
 * The risk appetite statement for one ICAAP cycle, and the capital plan's own
 * triggers measured against it.
 *
 * The two belong together: the appetite says where the institution wants to
 * sit, and the triggers say what it has promised the Board it will do when it
 * does not. The ordering rule and every regulatory reference are rendered from
 * the API payload; a metric with no governed regulatory value reads "not
 * assessed against a regulatory floor" (D-036) rather than being compared with
 * an invented one.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useBankContext } from "@/components/shell/BankContext";
import RiskAppetite from "@/components/icaap/p2/RiskAppetite";
import CapitalTriggers from "@/components/icaap/p2/CapitalTriggers";
import { use } from "react";

export default function IcaapAppetitePage({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = use(params);
  const { bank } = useBankContext();
  return (
    <PageContainer className="py-6">
      {bank && (
        <div className="space-y-4">
          <RiskAppetite bankId={bank.id} cycleId={cycleId} />
          <CapitalTriggers bankId={bank.id} cycleId={cycleId} />
        </div>
      )}
    </PageContainer>
  );
}
