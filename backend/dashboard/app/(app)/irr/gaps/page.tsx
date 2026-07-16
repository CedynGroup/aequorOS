'use client';

/**
 * Gap Analysis: full repricing ladder (RSA up, RSL down, net gap, cumulative
 * overlay) and the bucket detail table. Clicking a bucket expands the stored
 * run's matching `irr_gap` line item for provenance. All figures are engine
 * outputs; RSL is only negated for the mirrored presentation.
 */

import { useState } from 'react';
import type { IrrGapBucketRead } from '@aequoros/risk-service-api';
import IrrWorkspace from '@/components/irr/IrrWorkspace';
import RepricingLadderChart from '@/components/irr/charts/RepricingLadderChart';
import DataTable, { type Column } from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import RunBadge from '@/components/ui/RunBadge';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { num, shortId } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export default function IrrGapsPage() {
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);

  return (
    <IrrWorkspace
      crumb="Gap Analysis"
      subtitle="Repricing gap by tenor bucket — rate-sensitive assets vs liabilities"
    >
      {({ data, metrics: m, latestRun, computedAt }) => {
        const rows = data.gapTable ?? [];
        const runBadge = latestRun ? <RunBadge run={latestRun} /> : undefined;

        const ladder = rows.map((g) => ({
          bucket: g.bucket,
          rsa: num(g.rsaGhs),
          rsl: num(g.rslGhs),
          gap: num(g.gapGhs),
          cumulative: num(g.cumulativeGapGhs),
        }));

        const cum12m = num(m.cumulative12mGapGhs);

        const selected = rows.find((r) => r.bucket === selectedBucket) ?? null;
        const selectedLineItem =
          selected && latestRun
            ? latestRun.lineItems.find(
                (li) => li.section === 'irr_gap' && li.lineCode === selected.bucket
              ) ?? null
            : null;

        const columns: Column<IrrGapBucketRead>[] = [
          {
            key: 'bucket',
            header: 'Tenor bucket',
            width: '16%',
            render: (r) => (
              <span className="font-mono text-caption font-medium text-navy">
                {r.bucket}
              </span>
            ),
          },
          {
            key: 'midpoint',
            header: 'Midpoint',
            numeric: true,
            render: (r) => `${num(r.midpointYears).toFixed(2)}y`,
          },
          {
            key: 'rsa',
            header: 'RSA',
            numeric: true,
            render: (r) => fmtCurrency(num(r.rsaGhs), 'GHS'),
          },
          {
            key: 'rsl',
            header: 'RSL',
            numeric: true,
            render: (r) => fmtCurrency(num(r.rslGhs), 'GHS'),
          },
          {
            key: 'gap',
            header: 'Period gap',
            numeric: true,
            render: (r) => {
              const v = num(r.gapGhs);
              return (
                <span className={v < 0 ? 'text-warning font-medium' : undefined}>
                  {fmtCurrencySigned(v, 'GHS')}
                </span>
              );
            },
          },
          {
            key: 'cum',
            header: 'Cumulative gap',
            numeric: true,
            render: (r) => {
              const v = num(r.cumulativeGapGhs);
              return (
                <span className={v < 0 ? 'text-warning font-medium' : undefined}>
                  {fmtCurrencySigned(v, 'GHS')}
                </span>
              );
            },
          },
          {
            key: 'window',
            header: '≤12m',
            align: 'right',
            render: (r) =>
              r.within12m ? (
                <StatusPill tone="action">EaR window</StatusPill>
              ) : (
                <span className="text-caption text-slate-light">—</span>
              ),
          },
        ];

        return (
          <>
            {/* KPI row — all direct engine metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              <KpiStat
                label="12-month cumulative gap"
                value={fmtCurrencySigned(cum12m, 'GHS')}
                status={cum12m < 0 ? 'warn' : 'ok'}
                hint={
                  cum12m < 0
                    ? 'Liability-sensitive over 12 months'
                    : 'Asset-sensitive over 12 months'
                }
              />
              <KpiStat
                label="Duration gap"
                value={num(m.durationGap).toFixed(2)}
                unit="yrs"
                hint="Modified duration, assets − liabilities"
              />
              <KpiStat
                label="Asset duration"
                value={num(m.assetDuration).toFixed(2)}
                unit="yrs"
                hint="PV-weighted modified duration"
              />
              <KpiStat
                label="Liability duration"
                value={num(m.liabilityDuration).toFixed(2)}
                unit="yrs"
                hint="PV-weighted modified duration"
              />
            </div>

            <SectionCard
              title="Repricing ladder"
              subtitle="RSA plotted up, RSL down, net gap per bucket · cumulative gap overlay"
              computedAt={computedAt}
              runBadge={runBadge}
            >
              {ladder.length > 0 ? (
                <RepricingLadderChart data={ladder} height={360} />
              ) : (
                <p className="text-body text-slate">No repricing buckets for this period.</p>
              )}
            </SectionCard>

            <SectionCard
              title="Bucket detail"
              subtitle="Click a bucket to expand its stored-run line item"
              noPadding
              computedAt={computedAt}
              runBadge={runBadge}
            >
              <DataTable
                columns={columns}
                rows={rows}
                density="compact"
                onRowClick={(r) =>
                  setSelectedBucket((cur) => (cur === r.bucket ? null : r.bucket))
                }
                rowClassName={(r) =>
                  r.bucket === selectedBucket ? 'bg-action-light/40' : ''
                }
              />
              {selected && (
                <div className="border-t border-border bg-surface/60 px-5 py-4">
                  <div className="flex items-baseline justify-between gap-3 flex-wrap">
                    <p className="text-caption font-medium text-navy uppercase tracking-wider">
                      {selected.bucket} — bucket drill-down
                    </p>
                    {selected.within12m && (
                      <StatusPill tone="action">
                        Feeds ≤12m cumulative gap & EaR
                      </StatusPill>
                    )}
                  </div>
                  <dl className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-2 text-body">
                    <DrillField label="Repricing midpoint" value={`${num(selected.midpointYears).toFixed(3)} years`} />
                    <DrillField label="Rate-sensitive assets" value={fmtCurrency(num(selected.rsaGhs), 'GHS')} />
                    <DrillField label="Rate-sensitive liabilities" value={fmtCurrency(num(selected.rslGhs), 'GHS')} />
                    <DrillField
                      label="Period gap (RSA − RSL)"
                      value={fmtCurrencySigned(num(selected.gapGhs), 'GHS')}
                    />
                  </dl>
                  {selectedLineItem ? (
                    <div className="mt-3 rounded border border-border-light bg-surface-raised px-4 py-3">
                      <p className="text-caption text-slate">
                        Stored run line item{' '}
                        <span className="font-mono text-navy">
                          {selectedLineItem.section}/{selectedLineItem.lineCode}
                        </span>{' '}
                        · position {selectedLineItem.position}
                      </p>
                      <p className="mt-1 text-body text-navy">{selectedLineItem.description}</p>
                      <p className="mt-1 text-caption text-slate">
                        exposure{' '}
                        <span className="font-mono tnum text-navy">
                          {selectedLineItem.exposureAmount != null
                            ? fmtCurrency(num(selectedLineItem.exposureAmount), 'GHS')
                            : '—'}
                        </span>
                        {' · '}weighted (gap){' '}
                        <span className="font-mono tnum text-navy">
                          {fmtCurrencySigned(num(selectedLineItem.weightedAmount), 'GHS')}
                        </span>
                        {latestRun && (
                          <>
                            {' · '}run{' '}
                            <span className="font-mono text-navy">
                              {shortId(latestRun.id, 8)}
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                  ) : (
                    <p className="mt-3 text-caption text-slate">
                      No stored run for this period yet — figures above are the
                      inline computation. Run all scenarios to persist an
                      auditable line item for this bucket.
                    </p>
                  )}
                </div>
              )}
            </SectionCard>
          </>
        );
      }}
    </IrrWorkspace>
  );
}

function DrillField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro uppercase tracking-wider text-slate">{label}</dt>
      <dd className="mt-0.5 font-mono tnum text-navy">{value}</dd>
    </div>
  );
}
