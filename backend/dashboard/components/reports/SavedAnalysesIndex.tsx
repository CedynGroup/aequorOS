'use client';

/**
 * Cross-module saved-analyses index — fans the per-module saved-analyses
 * listing out across all five workbench modules (the same fan-out shape as
 * the Risk & Limit Monitor) and folds them into one governance table for
 * ALCO prep. Read-only: saving and deleting stay in the owning workbench.
 */

import Link from 'next/link';
import { ChevronRight, FlaskConical } from 'lucide-react';
import type {
  SavedAnalysisSummaryRead,
  WorkbenchModule,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { PageSkeleton } from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import { useSavedAnalyses } from '@/lib/api/hooks';
import { fmtTimestamp } from '@/lib/api/values';

const MODULE_LABELS: Record<WorkbenchModule, string> = {
  liquidity: 'Liquidity',
  capital: 'Capital',
  irr: 'Interest Rate Risk',
  fx: 'FX Risk',
  ftp: 'FTP',
};

/** The owning module's workbench tab — where each analysis was saved. */
const WORKBENCH_HREFS: Record<WorkbenchModule, string> = {
  liquidity: '/liquidity/stress',
  capital: '/basel/stress',
  irr: '/irr/scenarios',
  fx: '/fx/scenarios',
  ftp: '/ftp/scenarios',
};

type AnalysisRow = SavedAnalysisSummaryRead & { periodLabel: string | null };

export default function SavedAnalysesIndex() {
  const { bank, periods } = useBankContext();
  const bankId = bank?.id;

  const liquidity = useSavedAnalyses(bankId, 'liquidity');
  const capital = useSavedAnalyses(bankId, 'capital');
  const irr = useSavedAnalyses(bankId, 'irr');
  const fx = useSavedAnalyses(bankId, 'fx');
  const ftp = useSavedAnalyses(bankId, 'ftp');

  const moduleQueries = [
    ['liquidity', liquidity],
    ['capital', capital],
    ['irr', irr],
    ['fx', fx],
    ['ftp', ftp],
  ] as const;

  const isLoading = moduleQueries.some(([, query]) => query.isLoading);
  const unavailableModules = moduleQueries
    .filter(([, query]) => query.isError)
    .map(([module]) => MODULE_LABELS[module]);

  const periodLabels = new Map(periods.map((p) => [p.id, p.label]));
  const rows: AnalysisRow[] = moduleQueries
    .flatMap(([, query]) => query.data?.analyses ?? [])
    .map((entry) => ({
      ...entry,
      periodLabel: periodLabels.get(entry.reportingPeriodId) ?? null,
    }))
    .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());

  const columns: Column<AnalysisRow>[] = [
    {
      key: 'module',
      header: 'Module',
      width: '14%',
      render: (r) => (
        <StatusPill tone="slate">{MODULE_LABELS[r.module]}</StatusPill>
      ),
    },
    {
      key: 'name',
      header: 'Analysis',
      width: '30%',
      render: (r) => (
        <div className="min-w-0">
          <p className="font-medium text-navy truncate">{r.name}</p>
          <p className="text-caption text-slate">engine {r.engineVersion}</p>
        </div>
      ),
    },
    {
      key: 'scenarios',
      header: 'Scenarios',
      numeric: true,
      render: (r) => r.scenarioCount,
    },
    {
      key: 'period',
      header: 'Period',
      render: (r) =>
        r.periodLabel ?? <span className="text-caption text-slate">—</span>,
    },
    {
      key: 'created',
      header: 'Created',
      render: (r) => (
        <div>
          <p className="font-mono text-caption text-navy tnum">
            {fmtTimestamp(r.createdAt)}
          </p>
          <p className="text-caption text-slate">
            {r.createdBy ? `by ${r.createdBy}` : 'recorded without a user'}
          </p>
        </div>
      ),
    },
    {
      key: 'open',
      header: '',
      align: 'right',
      render: (r) => (
        <Link
          href={WORKBENCH_HREFS[r.module]}
          className="inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover whitespace-nowrap"
        >
          Open workbench
          <ChevronRight size={12} aria-hidden />
        </Link>
      ),
    },
  ];

  if (isLoading) return <PageSkeleton />;

  return (
    <SectionCard
      title="Saved analyses"
      subtitle="Every scenario analysis kept from the five treasury workbenches — snapshots recomputed server-side at save time"
      noPadding
      footer={
        unavailableModules.length > 0 ? (
          <span>
            Saved-analysis listing unavailable for:{' '}
            {unavailableModules.join(', ')}.
          </span>
        ) : (
          <span>
            Analyses are saved and deleted in the owning module&apos;s
            workbench; this index is read-only.
          </span>
        )
      }
    >
      {rows.length === 0 ? (
        <div className="p-5">
          <EmptyState
            Icon={FlaskConical}
            title="No saved analyses yet"
            description="Run scenarios in a module's Stress or Scenarios workbench and choose Save — the kept snapshots from all five modules gather here for ALCO prep."
          />
        </div>
      ) : (
        <DataTable columns={columns} rows={rows} />
      )}
    </SectionCard>
  );
}
