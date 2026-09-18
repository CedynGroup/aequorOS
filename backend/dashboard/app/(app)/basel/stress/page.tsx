'use client';

import PageContainer from '@/components/ui/PageContainer';
import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useModuleScope } from '@/components/shell/BankContext';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The capital
// lens leads here; the run is bank-wide, coupling solvency and liquidity.
export default function CapitalStress() {
  const isSdi = useModuleScope().institutionClass === 'sdi';
  return (
    <>
      <PageHeader
        eyebrow="Basel Capital"
        title={isSdi ? 'SDI Stress Workbench' : 'Enterprise Stress Workbench'}
      />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="capital" />
      </PageContainer>
    </>
  );
}
