'use client';

/**
 * Saved Analyses — the cross-module index of every saved scenario analysis
 * for ALCO prep. One governance surface over the five workbench modules;
 * the analyses themselves are created and deleted in the owning workbench.
 */

import PageHeader from '@/components/ui/PageHeader';
import SavedAnalysesIndex from '@/components/reports/SavedAnalysesIndex';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

export default function SavedAnalysesPage() {
  const { period } = useBankContext();

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance' },
          { label: 'Reports Library', href: '/reports' },
          { label: 'Saved Analyses' },
        ]}
        title="Saved Analyses"
        subtitle="ALCO prep · every saved scenario analysis across the five treasury workbenches"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <div className="px-8 py-6 space-y-6">
        <SavedAnalysesIndex />
      </div>
    </>
  );
}
