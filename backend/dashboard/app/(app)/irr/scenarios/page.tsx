'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';

// Enterprise stress workbench (docs/stress.md Phase 6): a governed macro
// scenario drives every engine into a 3-year Appendix II projection. The IRRBB
// lens leads here; the rate path feeds the enterprise coupling.
export default function IrrScenarios() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'IRRBB', href: '/irr' },
          { label: 'Scenarios' },
        ]}
        title="Enterprise Stress Workbench"
        subtitle="Macro scenario → all engines → 3-year projection to Appendix II — the rate path drives ΔEVE inside the enterprise run"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="irr" />
      </div>
    </>
  );
}
