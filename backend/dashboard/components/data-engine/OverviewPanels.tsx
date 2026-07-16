'use client';

/**
 * Data Engine overview: per-integration status cards fed by the ingestion
 * summary rollup, and the canonical-model KPI strip. This page is the
 * cross-source console; everything integration-specific lives in the tabs.
 */

import Link from 'next/link';
import {
  ArrowRight,
  CheckCircle2,
  Clock,
  FileSpreadsheet,
  Server,
  Webhook,
} from 'lucide-react';
import type {
  IngestionSourceSummaryRead,
  IngestionSummaryRead,
} from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useIngestionSummary } from '@/lib/api/ingestion';
import { INTEGRATIONS, type Integration } from './content';
import { BatchStatusPill, formatDateTime } from './shared';

const INTEGRATION_ICONS: Record<string, typeof Server> = {
  'excel-csv': FileSpreadsheet,
  api: Webhook,
  t24: Server,
  adapters: Server,
};

function combineSources(
  summary: IngestionSummaryRead | undefined,
  integration: Integration,
): {
  batches: number;
  accepted: number;
  warnings: number;
  lastBatchAt: Date | null;
  lastStatus: string | null;
} {
  const sources = (summary?.sources ?? []).filter(
    (source: IngestionSourceSummaryRead) =>
      integration.sourceSystems.includes(source.sourceSystem),
  );
  let latest: IngestionSourceSummaryRead | null = null;
  for (const source of sources) {
    if (
      source.lastBatchAt &&
      (!latest?.lastBatchAt || source.lastBatchAt > latest.lastBatchAt)
    ) {
      latest = source;
    }
  }
  return {
    batches: sources.reduce((sum, source) => sum + source.batches, 0),
    accepted: sources.reduce((sum, source) => sum + source.recordsAcceptedTotal, 0),
    warnings: sources.reduce((sum, source) => sum + source.recordsWarningTotal, 0),
    lastBatchAt: latest?.lastBatchAt ?? null,
    lastStatus: latest?.lastStatus ?? null,
  };
}

function StatusBadge({ status, label }: { status: Integration['status']; label: string }) {
  if (status === 'connected') {
    return (
      <span className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-success">
        <CheckCircle2 size={13} aria-hidden /> {label}
      </span>
    );
  }
  if (status === 'pending') {
    return (
      <span className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-slate">
        <Clock size={13} aria-hidden /> {label}
      </span>
    );
  }
  return <span className="ml-auto text-caption font-medium text-slate">{label}</span>;
}

function IntegrationCard({
  integration,
  summary,
}: {
  integration: Integration;
  summary: IngestionSummaryRead | undefined;
}) {
  const Icon = INTEGRATION_ICONS[integration.key] ?? Server;
  const stats = combineSources(summary, integration);
  const connected = integration.status === 'connected';

  return (
    <div className={`card p-5 flex flex-col ${connected ? 'border-l-4 border-l-success' : ''}`}>
      <div className="flex items-center gap-2">
        <Icon size={18} className={connected ? 'text-success' : 'text-slate'} aria-hidden />
        <h3 className="text-h3 text-navy">{integration.name}</h3>
        <StatusBadge status={integration.status} label={integration.statusLabel} />
      </div>
      <p className="mt-2 text-body text-slate leading-relaxed">{integration.description}</p>

      <div className="mt-3 pt-3 border-t border-border-light grid grid-cols-3 gap-3">
        <div>
          <p className="text-micro uppercase tracking-wider text-slate">Batches</p>
          <p className="mt-0.5 font-mono text-h3 text-navy">{stats.batches}</p>
        </div>
        <div>
          <p className="text-micro uppercase tracking-wider text-slate">Accepted</p>
          <p className="mt-0.5 font-mono text-h3 text-success">
            {stats.accepted.toLocaleString('en-GH')}
          </p>
        </div>
        <div>
          <p
            className="text-micro uppercase tracking-wider text-slate"
            title="Persisted rows flagged for data quality — not rejections."
          >
            Flagged
          </p>
          <p
            className={`mt-0.5 font-mono text-h3 ${stats.warnings > 0 ? 'text-warning' : 'text-slate'}`}
          >
            {stats.warnings.toLocaleString('en-GH')}
          </p>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 min-h-6">
        {stats.lastBatchAt ? (
          <>
            <span className="text-caption text-slate">
              Last ingestion{' '}
              <span className="font-mono text-navy">{formatDateTime(stats.lastBatchAt)}</span>
            </span>
            {stats.lastStatus && <BatchStatusPill status={stats.lastStatus} />}
          </>
        ) : (
          <span className="text-caption text-slate">
            {connected ? 'No batches yet' : 'No ingestion until the adapter ships'}
          </span>
        )}
        <Link
          href={integration.href}
          className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
        >
          Open <ArrowRight size={13} aria-hidden />
        </Link>
      </div>
    </div>
  );
}

export function IntegrationCards() {
  const { bank } = useBankContext();
  const summaryQuery = useIngestionSummary(bank?.id);

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-h2 text-navy">Integrations</h2>
        <p className="text-caption text-slate">
          Adapters translate each source into the canonical model — downstream modules
          never see source-system formats.
        </p>
      </div>
      {summaryQuery.isError && (
        <ErrorPanel
          error={summaryQuery.error}
          onRetry={() => void summaryQuery.refetch()}
          title="Could not load the ingestion summary"
        />
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {INTEGRATIONS.map((integration) => (
          <IntegrationCard
            key={integration.key}
            integration={integration}
            summary={summaryQuery.data}
          />
        ))}
      </div>
    </section>
  );
}

export function CanonicalSummaryStrip() {
  const { bank } = useBankContext();
  const summaryQuery = useIngestionSummary(bank?.id);

  if (summaryQuery.isPending) {
    return <SkeletonLine height={80} />;
  }
  if (summaryQuery.isError) {
    return null; // IntegrationCards already surfaces the error panel.
  }

  const counts = summaryQuery.data.canonicalCounts;
  const totalRecords =
    counts.positions +
    counts.counterparties +
    counts.glAccounts +
    counts.products +
    counts.referenceRows;

  const tiles: { label: string; value: string; tone?: string; title?: string }[] = [
    {
      label: 'Canonical records',
      value: totalRecords.toLocaleString('en-GH'),
      title:
        'Current-generation positions, counterparties, GL accounts, and products, plus the reference rows the modules consume.',
    },
    { label: 'Positions (current gen)', value: counts.positions.toLocaleString('en-GH') },
    { label: 'Counterparties', value: counts.counterparties.toLocaleString('en-GH') },
    { label: 'GL accounts', value: counts.glAccounts.toLocaleString('en-GH') },
    { label: 'Products', value: counts.products.toLocaleString('en-GH') },
    { label: 'Reference rows', value: counts.referenceRows.toLocaleString('en-GH') },
    {
      label: 'Activations',
      value: String(summaryQuery.data.activationsCount),
      title: 'Fact derivations + module recomputes run from this console.',
    },
    {
      label: 'Last activation',
      value: summaryQuery.data.lastActivationAt
        ? formatDateTime(summaryQuery.data.lastActivationAt)
        : '—',
    },
  ];

  return (
    <section className="space-y-3">
      <h2 className="text-h2 text-navy">Canonical model</h2>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-px bg-border-light rounded overflow-hidden border border-border-light">
        {tiles.map((tile) => (
          <div key={tile.label} className="bg-white px-3 py-2.5" title={tile.title}>
            <p className="text-micro uppercase tracking-wider text-slate">{tile.label}</p>
            <p className={`mt-1 font-mono text-h3 ${tile.tone ?? 'text-navy'} truncate`}>
              {tile.value}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
