'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

// Treasury workspace: depreciation scenarios (regulatory set + desk shocks)
// revaluing the open position live. Official FX runs and the NOP return
// live under Governance.
export default function FxScenarios() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk', href: '/fx' },
          { label: 'Scenarios' },
        ]}
        title="FX Scenario Workbench"
        subtitle="Depreciation shocks against the current open position — live analysis, side-by-side comparison"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench
          module="fx"
          primaryMetric="nop_pct_tier1"
          metrics={[
            { key: 'nop_pct_tier1', label: 'NOP / Tier 1', kind: 'pct' },
            { key: 'stressed_nop_pct_tier1', label: 'Stressed NOP / Tier 1', kind: 'pct' },
            { key: 'overall_nop_ghs', label: 'Overall NOP', kind: 'ghs' },
            { key: 'stressed_nop_ghs', label: 'Stressed NOP', kind: 'ghs' },
            { key: 'single_ccy_max_pct', label: 'Largest single ccy', kind: 'pct' },
          ]}
        />
      </div>
    </>
  );
}
