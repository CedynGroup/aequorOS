'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

// Treasury workspace: pick scenarios (regulatory or desk-authored), tweak
// shocks, run the analysis against current positions, compare side by side.
// Nothing here writes to the official register — formal runs and returns
// live under Governance.
export default function LiquidityStress() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Stress' },
        ]}
        title="Liquidity Stress Workbench"
        subtitle="Run-off, inflow and haircut scenarios against current positions — live analysis, side-by-side comparison"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench
          module="liquidity"
          primaryMetric="lcr_pct"
          metrics={[
            { key: 'lcr_pct', label: 'LCR', kind: 'pct' },
            { key: 'nsfr_pct', label: 'NSFR', kind: 'pct' },
            { key: 'hqla_total_ghs', label: 'HQLA', kind: 'ghs' },
            { key: 'net_outflows_30d_ghs', label: 'Net outflows 30d', kind: 'ghs' },
            { key: 'fx_funding_gap_ghs', label: 'FX funding gap', kind: 'ghs' },
          ]}
        />
      </div>
    </>
  );
}
