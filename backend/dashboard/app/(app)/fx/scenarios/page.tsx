'use client';

import PageContainer from '@/components/ui/PageContainer';
import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The FX lens
// leads here; the cedi-depreciation path revalues the open position in the run.
export default function FxScenarios() {
  return (
    <>
      <PageHeader
        eyebrow="FX"
        title="Enterprise Stress Workbench"
      />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="fx" />
      </PageContainer>
    </>
  );
}
