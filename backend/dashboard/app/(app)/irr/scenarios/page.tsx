'use client';

import PageHeader from '@/components/ui/PageHeader';
import ScenarioWorkbench from '@/components/workbench/ScenarioWorkbench';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

// Treasury workspace: the Basel rate-shock table plus desk-authored curve
// shocks (parallel / short-end / long-end / decay), computed live against
// current positions. The official IRRBB run register lives under
// Governance → Reports.
export default function IrrScenarios() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'IRRBB', href: '/irr' },
          { label: 'Scenarios' },
        ]}
        title="Rate Scenario Workbench"
        subtitle="ΔEVE under the Basel shock table and your own curve shocks — live analysis, side-by-side comparison"
      />
      <div className="px-8 py-6">
        <ScenarioWorkbench
          module="irr"
          primaryMetric="delta_eve_pct_tier1"
          metrics={[
            { key: 'delta_eve_pct_tier1', label: 'ΔEVE / Tier 1', kind: 'pct' },
            { key: 'delta_eve_ghs', label: 'ΔEVE', kind: 'ghs' },
            { key: 'base_eve_ghs', label: 'Base EVE', kind: 'ghs' },
            { key: 'shifted_eve_ghs', label: 'Shocked EVE', kind: 'ghs' },
            { key: 'ear_ghs', label: 'Earnings at risk', kind: 'ghs' },
          ]}
        />
      </div>
    </>
  );
}
