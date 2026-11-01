"use client";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import ScenarioWorkbench from "@/features/workbench/components/ScenarioWorkbench";
export default function ModuleScenariosPage() {
  return (
    <>
      <PageHeader eyebrow="Capital" title="Capital Scenarios" />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="capital" />
      </PageContainer>
    </>
  );
}
