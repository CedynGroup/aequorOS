"use client";

/**
 * Pillar 2 for one ICAAP cycle, in five views.
 *
 * REGISTER — the quantified items, Table 5 in the regulator's orientation, and
 * the source-consistency control.
 * RECONCILIATION — requirement against resources, with the eligibility of each
 * capital component and the controls that compare the ICAAP's figures with the
 * rest of the platform's.
 * ALLOCATION — how the internal requirement is shared across the institution.
 * SUPERVISORY ADD-ONS — capital the supervisor has required, and its letters.
 * GOVERNED VALUES — every control-plane value the assessment rested on, with
 * its citation and confirmation status (D-024).
 */

import { useState } from "react";
import PageContainer from "@/components/ui/PageContainer";
import SubTabs from "@/components/ui/SubTabs";
import { useBankContext } from "@/components/shell/BankContext";
import { useIcaapCycle } from "@/lib/api/icaap";
import Pillar2Register from "@/components/icaap/p2/Pillar2Register";
import CapitalReconciliation from "@/components/icaap/p2/CapitalReconciliation";
import CapitalAllocation from "@/components/icaap/p2/CapitalAllocation";
import SupervisoryAddons from "@/components/icaap/p2/SupervisoryAddons";
import ParameterRegister from "@/components/icaap/p2/ParameterRegister";

const VIEWS = [
  { key: "register", label: "Register" },
  { key: "reconciliation", label: "Reconciliation" },
  { key: "allocation", label: "Allocation" },
  { key: "addons", label: "Supervisory add-ons" },
  { key: "parameters", label: "Governed values" },
];

export default function IcaapPillar2Page({
  params,
}: {
  params: { cycleId: string };
}) {
  const { bank } = useBankContext();
  const cycleQuery = useIcaapCycle(bank?.id, params.cycleId);
  const [view, setView] = useState("register");

  return (
    <PageContainer className="py-6">
      <SubTabs items={VIEWS} active={view} onChange={setView} />
      <div className="mt-4">
        {bank && view === "register" && (
          <Pillar2Register bankId={bank.id} cycleId={params.cycleId} />
        )}
        {bank && view === "reconciliation" && (
          <CapitalReconciliation bankId={bank.id} cycleId={params.cycleId} />
        )}
        {bank && view === "allocation" && (
          <CapitalAllocation bankId={bank.id} cycleId={params.cycleId} />
        )}
        {bank && view === "addons" && (
          // The cycle's own date, so a letter's converted amount agrees with the
          // rest of this assessment rather than with today.
          <SupervisoryAddons
            bankId={bank.id}
            asOf={cycleQuery.data?.asOfDate ?? null}
          />
        )}
        {bank && view === "parameters" && (
          <ParameterRegister bankId={bank.id} cycleId={params.cycleId} />
        )}
      </div>
    </PageContainer>
  );
}
