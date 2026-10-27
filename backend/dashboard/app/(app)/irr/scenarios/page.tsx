"use client";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import ScenarioWorkbench from "@/components/workbench/ScenarioWorkbench";
export default function ModuleScenariosPage() {
  return (
    <>
      <PageHeader eyebrow="IRRBB" title="IRRBB Scenarios" />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="irr" />
      </PageContainer>
    </>
  );
}
