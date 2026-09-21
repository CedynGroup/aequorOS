"use client";

/**
 * Settings → the institution's ICAAP review chain.
 *
 * Outside the cycle shell on purpose: the chain belongs to the institution,
 * not to one assessment, and a cycle already under review keeps the chain it
 * was put forward under.
 */

import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { useBankContext } from "@/components/shell/BankContext";
import WorkflowTemplates from "@/components/icaap/p3/WorkflowTemplates";

export default function IcaapWorkflowSettingsPage() {
  const { bank } = useBankContext();

  return (
    <>
      <PageHeader
        title="ICAAP review chain"
        subtitle="Who has to read and approve this institution's capital adequacy assessment, and in what order."
        breadcrumbs={[{ label: "ICAAP", href: "/icaap" }, { label: "Review chain" }]}
      />
      <PageContainer className="py-6">
        {bank && <WorkflowTemplates bankId={bank.id} />}
      </PageContainer>
    </>
  );
}
