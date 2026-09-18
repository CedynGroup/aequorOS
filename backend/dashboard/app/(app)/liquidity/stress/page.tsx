'use client';

import PageContainer from '@/components/ui/PageContainer';
import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useModuleScope } from '@/components/shell/BankContext';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The
// liquidity lens leads here; the run couples solvency and liquidity (¶59(f)).
export default function LiquidityStress() {
  const isSdi = useModuleScope().institutionClass === 'sdi';
  return (
    <>
      <PageHeader
        eyebrow="Liquidity"
        title={isSdi ? 'SDI Stress Workbench' : 'Enterprise Stress Workbench'}
      />
      <PageContainer className="py-6">
        <ScenarioWorkbench module="liquidity" />
      </PageContainer>
    </>
  );
}
