'use client';

import { useState } from 'react';
import {
  CheckCircle2,
  CloudOff,
  Loader2,
  RotateCw,
} from 'lucide-react';
import type {
  BehavioralApplyRead,
  BehavioralProductEstimate,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonChart } from '@/components/ui/Skeleton';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import PrepaymentCurveChart from '@/components/charts/PrepaymentCurveChart';
import FeedsChip, { type Feed } from '@/components/behavioral/FeedsChip';
import ModelProvenanceCard from '@/components/behavioral/ModelProvenanceCard';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isServiceUnavailableError,
  useApplyBehavioralModel,
  useBehavioralModel,
  useTrainBehavioralModel,
  type BehavioralModelSlug,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

export type BehavioralPageConfig = {
  title: string;
  subtitle: string;
  valueLabel: string;
  /** Format a product value for display (e.g. "36 mo", "8.2%"). */
  format: (value: number) => string;
  /** KPI headline value + suffix. */
  avgValue: (value: number) => number;
  avgSuffix: string;
  avgDecimals: number;
  showCore?: boolean;
  showCurve?: boolean;
  /** ALM engines consuming this model's accepted assumptions. */
  feeds?: Feed[];
};

function confTone(c: number) {
  return c >= 0.5 ? 'compliant' : c >= 0.25 ? 'approaching' : 'pending';
}

function confStatus(c: number): 'ok' | 'warn' | 'crit' {
  return c >= 0.5 ? 'ok' : c >= 0.25 ? 'warn' : 'crit';
}

export default function BehavioralModelPage({
  slug,
  config,
}: {
  slug: BehavioralModelSlug;
  config: BehavioralPageConfig;
}) {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const query = useBehavioralModel(bankId, slug);
  const train = useTrainBehavioralModel(bankId, slug);
  const apply = useApplyBehavioralModel(bankId, slug);

  const result = query.data;
  const products = result?.products ?? [];
  const offline = isServiceUnavailableError(query.error);

  const [applied, setApplied] = useState<BehavioralApplyRead | null>(null);
  const curveProducts = products.filter((p) => p.incentiveCurve?.length);
  const [curveCode, setCurveCode] = useState<string | null>(null);
  const activeCurve =
    curveProducts.find((p) => p.productCode === curveCode) ?? curveProducts[0];

  const avg =
    products.length > 0
      ? products.reduce((s, p) => s + p.value, 0) / products.length
      : 0;
  const avgConf =
    products.length > 0
      ? products.reduce((s, p) => s + p.confidence, 0) / products.length
      : 0;

  const columns: Column<BehavioralProductEstimate>[] = [
    {
      key: 'product',
      header: 'Product',
      render: (p) => (
        <span className="font-mono text-caption text-navy">{p.productCode}</span>
      ),
    },
    {
      key: 'value',
      header: config.valueLabel,
      align: 'right',
      numeric: true,
      render: (p) => (
        <span className="font-mono tnum text-navy">{config.format(p.value)}</span>
      ),
    },
    ...(config.showCore
      ? [
          {
            key: 'core',
            header: 'Core %',
            align: 'right' as const,
            numeric: true,
            render: (p: BehavioralProductEstimate) => (
              <span className="font-mono tnum text-slate">
                {p.corePct != null ? `${(p.corePct * 100).toFixed(0)}%` : '—'}
              </span>
            ),
          },
        ]
      : []),
    {
      key: 'conf',
      header: 'Confidence',
      align: 'right',
      render: (p) => (
        <StatusPill tone={confTone(p.confidence)}>
          {(p.confidence * 100).toFixed(0)}%
        </StatusPill>
      ),
    },
    {
      key: 'method',
      header: 'Method',
      align: 'right',
      render: (p) => (
        <span className="text-micro font-medium uppercase tracking-wider text-slate">
          {p.method}
        </span>
      ),
    },
  ];

  const onApply = () => {
    apply.mutate(
      products.map((p) => ({
        productCode: p.productCode,
        value: p.value,
        unit: p.unit,
        confidence: p.confidence,
      })),
      { onSuccess: (r) => setApplied(r) }
    );
  };

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Behavioral Models', href: '/behavioral' },
          { label: config.title },
        ]}
        title={config.title}
        subtitle={
          <span className="inline-flex items-center gap-2 flex-wrap">
            {config.subtitle}
            {config.feeds?.map((feed) => (
              <FeedsChip key={feed.href + feed.label} feed={feed} />
            ))}
          </span>
        }
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <button
            type="button"
            onClick={() => train.mutate()}
            disabled={!bankId || train.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40"
          >
            {train.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <RotateCw size={13} aria-hidden />
            )}
            Retrain
          </button>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {offline ? (
          <div className="card border-l-4 border-l-critical bg-critical-light/40 p-5 flex items-start gap-3">
            <CloudOff size={18} className="text-critical shrink-0 mt-0.5" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-body font-medium text-navy">This model is unavailable</p>
              <p className="mt-1 text-body text-navy/80 leading-relaxed">
                The backend could not load the behavioral ML runtime. Check the backend
                logs, then retry.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
            >
              <RotateCw size={13} aria-hidden />
              Retry
            </button>
          </div>
        ) : query.error ? (
          <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
        ) : query.isLoading ? (
          <div className="space-y-3">
            <SkeletonChart height={260} />
            <p className="text-caption text-slate flex items-center gap-2">
              <Loader2 size={13} className="animate-spin" aria-hidden />
              Training on first request — the model fits on the bank&apos;s ingested history
              before responding.
            </p>
          </div>
        ) : result ? (
          <>
            {/* Headline KPIs — all straight off the model payload */}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              <KpiStat
                label={`Average ${config.valueLabel.toLowerCase()}`}
                value={config.avgValue(avg).toFixed(config.avgDecimals)}
                unit={config.avgSuffix}
                hint={`across ${products.length} product${products.length === 1 ? '' : 's'}`}
              />
              <KpiStat
                label="Weighted confidence"
                value={(avgConf * 100).toFixed(0)}
                unit="%"
                status={confStatus(avgConf)}
                hint="mean per-product estimator confidence"
              />
              <KpiStat
                label="Holdout CV error"
                value={
                  result.accuracy.cvRmse != null
                    ? result.accuracy.cvRmse.toFixed(3)
                    : '—'
                }
                unit={result.accuracy.cvRmse != null ? 'RMSE' : undefined}
                hint={
                  result.accuracy.cvRmse == null
                    ? 'not cross-validated'
                    : result.accuracy.cvMae != null
                    ? `time-series CV · MAE ${result.accuracy.cvMae.toFixed(3)}`
                    : 'time-series CV'
                }
              />
              <KpiStat
                label="Training data"
                value={result.accuracy.sampleCount.toLocaleString('en-US')}
                unit="rows"
                status={result.method === 'ml' ? 'ok' : 'warn'}
                hint={`${result.accuracy.monthCoverage} months · ${result.method.toUpperCase()}`}
              />
            </div>

            <ModelProvenanceCard result={result} />

            {config.showCurve && activeCurve?.incentiveCurve && (
              <SectionCard
                title="Prepayment sensitivity"
                subtitle="Modelled annual CPR vs rate incentive (note rate − refinance rate)"
                actions={
                  <div className="flex flex-wrap gap-1">
                    {curveProducts.map((p) => (
                      <button
                        key={p.productCode}
                        type="button"
                        onClick={() => setCurveCode(p.productCode)}
                        className={`px-2 py-1 rounded text-micro font-mono transition-colors ${
                          activeCurve.productCode === p.productCode
                            ? 'bg-action text-white'
                            : 'text-slate hover:bg-surface'
                        }`}
                      >
                        {p.productCode}
                      </button>
                    ))}
                  </div>
                }
                footer={
                  <span>
                    model{' '}
                    <span className="font-mono text-navy">{result.modelVersion}</span>
                    {result.asOfDate && (
                      <>
                        {' '}· trained as of{' '}
                        <span className="font-mono tnum text-navy">
                          {fmtDateUTC(result.asOfDate)}
                        </span>
                      </>
                    )}
                  </span>
                }
              >
                <PrepaymentCurveChart curve={activeCurve.incentiveCurve} />
              </SectionCard>
            )}

            <SectionCard
              title="Per-product estimates"
              subtitle="Learned from this bank's ingested canonical history"
              actions={
                result.asOfDate ? (
                  <span className="text-caption text-slate">
                    as of{' '}
                    <span className="font-mono text-navy">
                      {fmtDateUTC(result.asOfDate)}
                    </span>
                  </span>
                ) : undefined
              }
              noPadding
            >
              {products.length ? (
                <DataTable columns={columns} rows={products} density="comfortable" />
              ) : (
                <p className="px-5 py-4 text-body text-slate">
                  No canonical history available for this bank yet — ingest position data to
                  train the model.
                </p>
              )}
            </SectionCard>

            {products.length > 0 && (
              <SectionCard
                title="Apply as reviewed assumptions"
                subtitle="Writes these estimates through the acceptance workflow; the ALM engines consume them on the next recompute"
              >
                <div className="space-y-4">
                  <p className="text-body text-navy/85 leading-relaxed">
                    Review the estimates above. Applying records them as a new accepted
                    behavioral-assumptions batch with model provenance (SR 11-7), preserving
                    the other models&apos; current assumptions.
                  </p>
                  {applied ? (
                    <div className="flex items-start gap-3 rounded border border-success/30 bg-success-light/40 p-3">
                      <CheckCircle2 size={18} className="text-success shrink-0 mt-0.5" aria-hidden />
                      <div className="text-body text-navy">
                        Applied {applied.appliedRows} estimate
                        {applied.appliedRows === 1 ? '' : 's'} as of{' '}
                        <span className="font-mono">{fmtDateUTC(applied.asOfDate)}</span> (
                        {applied.totalRows} total assumption rows).
                        <span className="block text-caption text-slate mt-0.5 font-mono">
                          batch {applied.ingestionBatchId.slice(0, 8)}…
                        </span>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={onApply}
                      disabled={apply.isPending}
                      className="inline-flex items-center gap-2 px-4 py-2 text-caption font-medium btn-primary disabled:opacity-40"
                    >
                      {apply.isPending && <Loader2 size={14} className="animate-spin" aria-hidden />}
                      Apply {products.length} estimates
                    </button>
                  )}
                  {apply.error ? (
                    <p className="text-caption text-critical">
                      Could not apply — {String((apply.error as Error).message)}
                    </p>
                  ) : null}
                </div>
              </SectionCard>
            )}
          </>
        ) : null}
      </div>
    </>
  );
}
