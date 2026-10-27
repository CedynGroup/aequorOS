"use client";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EnterpriseStressWorkbench from "@/components/stress/EnterpriseStressWorkbench";
export default function EnterpriseStressPage() {
  return (
    <>
      <PageHeader
        eyebrow="Enterprise stress"
        title="Enterprise Stress Workbench"
      />
      <PageContainer className="py-6">
        <EnterpriseStressWorkbench />
      </PageContainer>
    </>
  );
}
