'use client';

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
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: isSdi ? 'Regulatory Capital' : 'Basel Capital', href: '/basel' },
          { label: 'Stress' },
        ]}
        title={isSdi ? 'SDI Stress Workbench' : 'Enterprise Stress Workbench'}
        subtitle={isSdi ? 'Section 29 capital and material-risk stress with a controlled SDI liquidity not-assessed disclosure.' : 'Macro scenario → all engines → 3-year projection to Appendix II, base vs stress, with driver attribution and management actions'}
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench module="capital" />
      </div>
    </>
  );
}
