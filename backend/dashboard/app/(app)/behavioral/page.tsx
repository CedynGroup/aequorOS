'use client';

/**
 * Behavioral model registry: one card per tenant-scoped model (NMD duration,
 * prepayment, deposit stability) with method, headline estimate, confidence,
 * training coverage and version provenance — every figure off the model
 * payload. Links into each model's detail workspace.
 */

import Link from 'next/link';
import { ArrowUpRight, Brain, Droplets, Loader2 } from 'lucide-react';
import type { BehavioralModelRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonCard } from '@/components/ui/Skeleton';
import FeedsChip, { type Feed } from '@/components/behavioral/FeedsChip';
import { useBankContext, useModuleScope } from '@/components/shell/BankContext';
import SdiModuleContext from '@/components/sdi/SdiModuleContext';
import {
  isServiceUnavailableError,
  useBehavioralLiquidity,
  useBehavioralModel,
  type BehavioralModelSlug,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

type RegistryEntry = {
  slug: BehavioralModelSlug;
  title: string;
  description: string;
  valueLabel: string;
  format: (v: number) => string;
  feeds: Feed[];
};

const REGISTRY: RegistryEntry[] = [
  {
    slug: 'nmd-duration',
    title: 'NMD Duration',
    description:
      'Effective behavioral duration of non-maturity deposits, learned from balance history.',
    valueLabel: 'Avg effective duration',
    format: (v) => `${v.toFixed(0)} mo`,
    feeds: [
      { label: 'IRRBB', href: '/irr' },
      { label: 'FTP', href: '/ftp' },
    ],
  },
  {
    slug: 'prepayment',
    title: 'Loan Prepayment',
    description:
      'Annual conditional prepayment rate per loan product, learned from realized unscheduled principal.',
    valueLabel: 'Avg annual CPR',
    format: (v) => `${(v * 100).toFixed(1)}%`,
    feeds: [
      { label: 'LCR', sdiLabel: 'Liquidity', href: '/liquidity' },
      { label: 'Cash-flow', href: '/liquidity/forecast' },
    ],
  },
  {
    slug: 'deposit-stability',
    title: 'Deposit Stability',
    description:
      'Stable (sticky) fraction of each deposit product under stress, learned from balance retention.',
    valueLabel: 'Avg stable fraction',
    format: (v) => `${(v * 100).toFixed(0)}%`,
    feeds: [{ label: 'LCR', sdiLabel: 'Liquidity', href: '/liquidity' }],
  },
];

function confTone(c: number) {
  return c >= 0.5 ? 'compliant' : c >= 0.25 ? 'approaching' : 'pending';
}

export default function BehavioralOverviewPage() {
  const { bank, period } = useBankContext();
  const scope = useModuleScope();
  const bankId = bank?.id;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Behavioral Models' },
          { label: 'Overview' },
        ]}
        title="Behavioral Models"
        subtitle="Tenant-scoped ML estimators feeding the ALM engines through the reviewed-assumption workflow"
      />

      <SdiModuleContext title="SDI ALM context">
        Deposit stability and non-maturity-deposit assumptions support proportionate liquidity and balance-sheet management. Forecast quality improves as more history is ingested.
      </SdiModuleContext>

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-4 gap-6">
          {REGISTRY.map((entry) => (
            <ModelRegistryCard key={entry.slug} entry={entry} bankId={bankId} />
          ))}
          <BehavioralLiquidityCard bankId={bankId} />
        </div>

        <SectionCard
          title="How assumptions reach the engines"
          subtitle="Model outputs never flow silently into the ALM engines"
        >
          <p className="text-body text-navy/85 leading-relaxed">
            Each model trains on the bank&apos;s ingested canonical history and
            proposes per-product estimates with a confidence score. Estimates
            take effect only when a reviewer applies them, which records a new
            accepted behavioral-assumptions batch with full model provenance
            (SR 11-7) — the {scope.institutionClass === 'sdi'
              ? 'IRRBB, liquidity-management, and forecasting engines'
              : 'IRRBB, LCR, FTP, and forecasting engines'} consume the accepted
            batch on their next recompute.
          </p>
        </SectionCard>
      </div>
    </>
  );
}

function BehavioralLiquidityCard({ bankId }: { bankId: string | undefined }) {
  const query = useBehavioralLiquidity(bankId);
  const segments = query.data?.segments ?? [];
  const scenarios = query.data?.scenarios ?? [];
  const ready = segments.filter((segment) => segment.dataStatus === 'ready').length;

  return (
    <div className="card p-5 h-full flex flex-col gap-4 hover:border-action/40 transition-colors">
      <Link href="/behavioral/liquidity" className="group flex items-start justify-between gap-3">
        <p className="inline-flex items-center gap-2 text-caption font-medium text-slate uppercase tracking-wider group-hover:text-navy transition-colors">
          <Droplets size={14} className="text-action" aria-hidden />
          Liquidity Behavior
        </p>
        <ArrowUpRight size={14} className="text-slate group-hover:text-action transition-colors shrink-0" aria-hidden />
      </Link>
      {query.isLoading ? (
        <p className="text-body text-slate">Loading observed deposit behavior...</p>
      ) : query.error ? (
        <p className="text-body text-slate">Open the workspace to review data readiness.</p>
      ) : (
        <>
          <div>
            <p className="text-micro uppercase tracking-wider text-slate">Evidence-ready segments</p>
            <p className="mt-1 font-mono text-kpi text-navy tnum">{ready} / {segments.length}</p>
            <p className="mt-1 text-caption text-slate">Runoff, churn proxy, seasonality, beta, and lag</p>
          </div>
          <p className="text-caption text-slate leading-relaxed">{scenarios.length} approved CFP behavioral scenario{scenarios.length === 1 ? '' : 's'} linked to documented actions.</p>
        </>
      )}
    </div>
  );
}

function ModelRegistryCard({
  entry,
  bankId,
}: {
  entry: RegistryEntry;
  bankId: string | undefined;
}) {
  const query = useBehavioralModel(bankId, entry.slug);
  const result: BehavioralModelRead | undefined = query.data;
  const offline = isServiceUnavailableError(query.error);
  const href = `/behavioral/${entry.slug}`;

  if (query.isLoading) {
    return (
      <div className="space-y-2">
        <SkeletonCard />
        <p className="text-caption text-slate flex items-center gap-2 px-1">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          Training on first request…
        </p>
      </div>
    );
  }

  const products = result?.products ?? [];
  const avg =
    products.length > 0
      ? products.reduce((s, p) => s + p.value, 0) / products.length
      : null;
  const avgConf =
    products.length > 0
      ? products.reduce((s, p) => s + p.confidence, 0) / products.length
      : null;

  return (
    <div className="card p-5 h-full flex flex-col gap-4 hover:border-action/40 transition-colors">
      <Link
        href={href}
        className="group flex items-start justify-between gap-3"
      >
        <p className="inline-flex items-center gap-2 text-caption font-medium text-slate uppercase tracking-wider group-hover:text-navy transition-colors">
          <Brain size={14} className="text-action" aria-hidden />
          {entry.title}
        </p>
        <ArrowUpRight
          size={14}
          className="text-slate group-hover:text-action transition-colors shrink-0"
          aria-hidden
        />
      </Link>

        {offline || query.error ? (
          <p className="text-body text-slate leading-relaxed">
            {offline
              ? 'Model runtime unavailable — open the model page to retry.'
              : 'Could not load this model — open the model page for details.'}
          </p>
        ) : (
          <>
            <div>
              <p className="text-micro uppercase tracking-wider text-slate">
                {entry.valueLabel}
              </p>
              <p className="mt-1 font-mono text-kpi text-navy tnum">
                {avg != null ? entry.format(avg) : '—'}
              </p>
              <p className="mt-1 text-caption text-slate">
                {products.length} product{products.length === 1 ? '' : 's'}
                {result?.asOfDate && (
                  <>
                    {' '}· as of{' '}
                    <span className="font-mono text-navy">
                      {fmtDateUTC(result.asOfDate)}
                    </span>
                  </>
                )}
              </p>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              {result && (
                <StatusPill tone={result.method === 'ml' ? 'compliant' : 'pending'}>
                  {result.method === 'ml' ? 'ML' : 'Baseline'}
                </StatusPill>
              )}
              {avgConf != null && (
                <StatusPill tone={confTone(avgConf)}>
                  {(avgConf * 100).toFixed(0)}% confidence
                </StatusPill>
              )}
            </div>
          </>
        )}

      <p className="text-caption text-slate leading-relaxed">{entry.description}</p>

      <div className="mt-auto border-t border-border-light -mx-5 px-5 pt-3 flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-micro uppercase tracking-wider text-slate">Feeds</span>
          {entry.feeds.map((feed) => (
            <FeedsChip key={feed.href + feed.label} feed={feed} />
          ))}
        </span>
        {result && (
          <span className="font-mono text-micro text-slate" title={result.modelId}>
            {result.modelVersion}
          </span>
        )}
      </div>
    </div>
  );
}
