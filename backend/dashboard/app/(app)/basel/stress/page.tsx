'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

// Treasury workspace: four-quarter capital paths under selectable and
// desk-authored downturn scenarios — live analysis only. Formal runs and
// the CAR return live under Governance.
export default function CapitalStress() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Stress' },
        ]}
        title="Capital Stress Workbench"
        subtitle="Credit-loss, RWA-growth and FX shocks over the four-quarter capital path — live analysis, side-by-side comparison"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench
          module="capital"
          primaryMetric="car_pct"
          metrics={[
            { key: 'car_pct', label: 'CAR', kind: 'pct' },
            { key: 'cet1_ratio_pct', label: 'CET1', kind: 'pct' },
            { key: 'worst_quarter_cet1_pct', label: 'Worst-quarter CET1', kind: 'pct' },
            { key: 'total_rwa_ghs', label: 'Total RWA', kind: 'ghs' },
            { key: 'total_capital_ghs', label: 'Total capital', kind: 'ghs' },
          ]}
        />
      </div>
    </>
  );
}
