'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The
// liquidity lens leads here; the run couples solvency and liquidity (¶59(f)).
export default function LiquidityStress() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Stress' },
        ]}
        title="Enterprise Stress Workbench"
        subtitle="Macro scenario → all engines → 3-year projection to Appendix II, with the LCR/NSFR path coupled to the solvency outcome"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="liquidity" />
      </div>
    </>
  );
}
