'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

// Treasury workspace: curve-shift and funding-spread overlays repricing the
// full book live. Official FTP runs live under Governance.
export default function FtpScenarios() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FTP', href: '/ftp' },
          { label: 'Scenarios' },
        ]}
        title="FTP Scenario Workbench"
        subtitle="Curve and funding-spread overlays repricing the book — live analysis, side-by-side comparison"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench
          module="ftp"
          primaryMetric="portfolio_nim_pct"
          metrics={[
            { key: 'portfolio_nim_pct', label: 'Portfolio NIM', kind: 'pct' },
            { key: 'total_contribution_ghs', label: 'Total contribution', kind: 'ghs' },
            { key: 'products_below_min_margin', label: 'Below margin floor', kind: 'number' },
            { key: 'curve_shift_pct', label: 'Curve shift', kind: 'pct' },
            { key: 'nmd_core_pct', label: 'NMD core', kind: 'pct' },
          ]}
        />
      </div>
    </>
  );
}
