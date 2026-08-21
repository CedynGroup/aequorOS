'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The FX lens
// leads here; the cedi-depreciation path revalues the open position in the run.
export default function FxScenarios() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk', href: '/fx' },
          { label: 'Scenarios' },
        ]}
        title="Enterprise Stress Workbench"
        subtitle="Macro scenario → all engines → 3-year projection to Appendix II — the FX path revalues the open position inside the enterprise run"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="fx" />
      </div>
    </>
  );
}
