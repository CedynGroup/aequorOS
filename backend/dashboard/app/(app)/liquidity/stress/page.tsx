"use client";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import ScenarioWorkbench from "@/components/workbench/ScenarioWorkbench";
export default function ModuleScenariosPage() {
  return (
    <>
      <PageHeader eyebrow="Liquidity" title="Liquidity Scenarios" />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="liquidity" />
      </PageContainer>
    </>
  );
}
