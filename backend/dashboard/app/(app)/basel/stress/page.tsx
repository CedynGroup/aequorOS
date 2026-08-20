'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The capital
// lens leads here; the run is bank-wide, coupling solvency and liquidity.
export default function CapitalStress() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Stress' },
        ]}
        title="Enterprise Stress Workbench"
        subtitle="Macro scenario → all engines → 3-year projection to Appendix II, base vs stress, with driver attribution and management actions"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="capital" />
      </div>
    </>
  );
}
