'use client';

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
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Stress' },
        ]}
        title={isSdi ? 'SDI Stress Workbench' : 'Enterprise Stress Workbench'}
        subtitle={isSdi ? 'Simplified capital and material-risk stress. SDI liquidity stress remains not assessed until the BoG method is configured.' : 'Macro scenario → all engines → 3-year projection to Appendix II, with the LCR/NSFR path coupled to the solvency outcome'}
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="liquidity" />
      </div>
    </>
  );
}
