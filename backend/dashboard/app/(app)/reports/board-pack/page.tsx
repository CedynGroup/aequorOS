'use client';

/**
 * Board Pack — a print-optimized composite report: cover page, cross-module
 * executive summary from the live engine, and one-page module briefs with
 * metric tables, validation counts, and official-run provenance.
 *
 * Print fidelity comes from app/print.css (imported here only): A4 pages,
 * per-section breaks, forced light palette. Tables and KPIs, not charts.
 */

import '@/app/print.css';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, Printer } from 'lucide-react';
import type { LiveStatus } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { SkeletonCard, SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  LIVE_MODULE_LABELS,
  livePrimaryMetric,
} from '@/components/live/moduleDisplay';
import {
  BoardPage,
  ModuleBrief,
  type MetricRow,
} from '@/components/reports/BoardPackSections';
import { useLatestRunsByModule } from '@/components/reports/hooks';
import {
  useBankAlerts,
  useCapitalDashboard,
  useFtpDashboard,
  useFxDashboard,
  useIrrDashboard,
  useLiquidityDashboard,
  useLiveSummary,
} from '@/lib/api/hooks';
import {
  fmtDateUTC,
  fmtTimestamp,
  labelize,
  num,
  statusTone,
} from '@/lib/api/values';
import { fmtCurrency, fmtNum, fmtPct } from '@/lib/format';

const LIVE_KPI_STATUS: Record<LiveStatus, KpiStatus | undefined> = {
  green: 'ok',
  amber: 'warn',
  red: 'crit',
  na: undefined,
};

export default function BoardPackPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const live = useLiveSummary(bankId);
  const alerts = useBankAlerts(bankId);
  const liq = useLiquidityDashboard(bankId, periodId);
  const cap = useCapitalDashboard(bankId, periodId);
  const irr = useIrrDashboard(bankId, periodId);
  const fx = useFxDashboard(bankId, periodId);
  const ftp = useFtpDashboard(bankId, periodId);
  const { byModule } = useLatestRunsByModule(bankId);

  // Client-only timestamp — avoids an SSR/hydration mismatch on the cover.
  const [generatedAt, setGeneratedAt] = useState<Date | null>(null);
  useEffect(() => {
    setGeneratedAt(new Date());
  }, []);

  if (!period) {
    return (
      <>
        <div className="no-print">
          <PageHeader
            breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Board pack' }]}
            title="Board Pack"
            subtitle="Print-optimized executive report"
          />
        </div>
        <div className="px-8 py-6">
          <EmptyState
            title="No reporting period"
            description="Upload and activate data in the Data Engine to compose a board pack for an as-of period."
          />
        </div>
      </>
    );
  }

  return (
    <>
      {/* Screen-only toolbar — the print pipeline never sees it. */}
      <div className="no-print">
        <PageHeader
          breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Board pack' }]}
          title="Board Pack"
          subtitle="Cover · executive summary · module briefs — A4 print layout"
          asOf={fmtDateUTC(period.periodEnd)}
          action={
            <div className="flex items-center gap-2">
              <Link
                href="/reports"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface hover:text-navy"
              >
                <ArrowLeft size={13} aria-hidden />
                Reports library
              </Link>
              <button
                type="button"
                onClick={() => window.print()}
                className="btn-primary inline-flex items-center gap-2 px-4 py-2 text-caption font-medium"
              >
                <Printer size={14} aria-hidden />
                Print / Save as PDF
              </button>
            </div>
          }
        />
      </div>

      <div className="board-pack px-8 py-6 space-y-6 max-w-4xl mx-auto">
        {/* ------------------------------------------------------------ */}
        {/* Cover page                                                    */}
        {/* ------------------------------------------------------------ */}
        <BoardPage>
          <div className="card px-8 py-12 flex flex-col gap-8 bp-avoid-break">
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate">
                AequorOS · Regulatory Command
              </p>
              <h1 className="mt-3 text-display text-navy">
                Board Risk &amp; Regulatory Pack
              </h1>
              <p className="mt-2 text-body text-slate">
                Cross-module risk position, regulatory ratios, and calculation
                provenance for board review.
              </p>
            </div>

            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-5 text-body">
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Institution
                </dt>
                <dd className="mt-1 text-h3 text-navy">{bank?.name ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  License · Regulator
                </dt>
                <dd className="mt-1 text-navy">
                  {bank ? labelize(bank.licenseType) : '—'} · Bank of Ghana
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  As-of period
                </dt>
                <dd className="mt-1 text-navy">
                  {period.label} ·{' '}
                  <span className="font-mono tnum">
                    {fmtDateUTC(period.periodEnd)}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Generated
                </dt>
                <dd className="mt-1 font-mono text-navy tnum">
                  {generatedAt ? fmtTimestamp(generatedAt) : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Reporting currency
                </dt>
                <dd className="mt-1 font-mono text-navy">
                  {bank?.currency ?? '—'}
                </dd>
              </div>
              <div>
                <dt className="text-micro font-medium uppercase tracking-wider text-slate">
                  Data state
                </dt>
                <dd className="mt-1 text-navy">
                  {live.data
                    ? live.data.isStale
                      ? 'Changed since last official run'
                      : 'In sync with official runs'
                    : '—'}
                </dd>
              </div>
            </dl>

            <p className="text-caption text-slate leading-relaxed border-t border-border-light pt-4">
              All figures are computed server-side by deterministic regulatory
              engines. Each module brief carries the engine version and input
              hash of its latest official run — identical inputs reproduce
              identical outputs. Synthetic demonstration dataset; no production
              bank data.
            </p>
          </div>
        </BoardPage>

        {/* ------------------------------------------------------------ */}
        {/* Executive summary                                             */}
        {/* ------------------------------------------------------------ */}
        <BoardPage>
          <h2 className="text-h2 text-navy mb-1">Executive summary</h2>
          <p className="text-caption text-slate mb-4">
            Live cross-module position for {period.label} — headline metric and
            traffic-light status per module.
          </p>
          <QueryBoundary
            isLoading={live.isLoading}
            error={live.error}
            onRetry={() => live.refetch()}
            skeleton={
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {[1, 2, 3, 4, 5, 6].map((i) => (
                  <SkeletonCard key={i} />
                ))}
              </div>
            }
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {(live.data?.modules ?? []).map((view) => {
                const metric = livePrimaryMetric(
                  view.module,
                  view.metrics as Record<string, unknown>
                );
                return (
                  <KpiStat
                    key={view.module}
                    label={LIVE_MODULE_LABELS[view.module]}
                    value={metric?.value ?? '—'}
                    hint={metric?.label ?? 'Not yet computed'}
                    status={LIVE_KPI_STATUS[view.status]}
                    className="bp-avoid-break"
                  />
                );
              })}
              <KpiStat
                label="Open breach alerts"
                value={alerts.data ? fmtNum(alerts.data.total) : '—'}
                hint="Across all module limits"
                status={
                  alerts.data
                    ? alerts.data.total > 0
                      ? 'crit'
                      : 'ok'
                    : undefined
                }
                className="bp-avoid-break"
              />
            </div>
          </QueryBoundary>
        </BoardPage>

        {/* ------------------------------------------------------------ */}
        {/* Module briefs — one printable page each                       */}
        {/* ------------------------------------------------------------ */}
        <BoardPage>
          <QueryBoundary
            isLoading={liq.isLoading}
            error={liq.error}
            onRetry={() => liq.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {liq.data && (
              <ModuleBrief
                name="Liquidity"
                code="02"
                statusTone={statusTone(liq.data.metrics.lcrStatus)}
                statusLabel={`LCR ${labelize(liq.data.metrics.lcrStatus)}`}
                run={byModule.get('liquidity')}
                computedAt={liq.data.live?.computedAt}
                rows={[
                  {
                    label: 'Liquidity Coverage Ratio',
                    hint: 'BoG minimum 100%',
                    value: fmtPct(num(liq.data.metrics.lcrPct), 2),
                    tone: statusTone(liq.data.metrics.lcrStatus),
                    toneLabel: labelize(liq.data.metrics.lcrStatus),
                  },
                  {
                    label: 'Net Stable Funding Ratio',
                    hint: 'BoG minimum 100%',
                    value: fmtPct(num(liq.data.metrics.nsfrPct), 2),
                    tone: statusTone(liq.data.metrics.nsfrStatus),
                    toneLabel: labelize(liq.data.metrics.nsfrStatus),
                  },
                  {
                    label: 'High-quality liquid assets',
                    value: fmtCurrency(num(liq.data.metrics.hqlaTotalGhs)),
                  },
                  {
                    label: 'Net outflows (30 days)',
                    value: fmtCurrency(num(liq.data.metrics.netOutflows30dGhs)),
                  },
                ]}
              />
            )}
          </QueryBoundary>
        </BoardPage>

        <BoardPage>
          <QueryBoundary
            isLoading={cap.isLoading}
            error={cap.error}
            onRetry={() => cap.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {cap.data && (
              <ModuleBrief
                name="Basel Capital"
                code="04"
                statusTone={statusTone(cap.data.metrics.carStatus)}
                statusLabel={`CAR ${labelize(cap.data.metrics.carStatus)}`}
                validations={{
                  total: cap.data.validations.length,
                  failed: cap.data.validations.filter((v) => !v.passed).length,
                }}
                run={byModule.get('capital')}
                computedAt={cap.data.live?.computedAt}
                rows={[
                  {
                    label: 'Capital Adequacy Ratio',
                    hint: 'CRD minimum 13% incl. buffers',
                    value: fmtPct(num(cap.data.metrics.carPct), 2),
                    tone: statusTone(cap.data.metrics.carStatus),
                    toneLabel: labelize(cap.data.metrics.carStatus),
                  },
                  {
                    label: 'CET1 ratio',
                    value: fmtPct(num(cap.data.metrics.cet1RatioPct), 2),
                    tone: statusTone(cap.data.metrics.cet1Status),
                    toneLabel: labelize(cap.data.metrics.cet1Status),
                  },
                  {
                    label: 'Tier 1 ratio',
                    value: fmtPct(num(cap.data.metrics.tier1RatioPct), 2),
                    tone: statusTone(cap.data.metrics.tier1Status),
                    toneLabel: labelize(cap.data.metrics.tier1Status),
                  },
                  {
                    label: 'Leverage ratio',
                    value: fmtPct(num(cap.data.metrics.leverageRatioPct), 2),
                    tone: statusTone(cap.data.metrics.leverageStatus),
                    toneLabel: labelize(cap.data.metrics.leverageStatus),
                  },
                  {
                    label: 'Total risk-weighted assets',
                    value: fmtCurrency(num(cap.data.metrics.totalRwaGhs)),
                  },
                  {
                    label: 'Total regulatory capital',
                    value: fmtCurrency(num(cap.data.metrics.totalCapitalGhs)),
                  },
                ]}
              />
            )}
          </QueryBoundary>
        </BoardPage>

        <BoardPage>
          <QueryBoundary
            isLoading={irr.isLoading}
            error={irr.error}
            onRetry={() => irr.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {irr.data && (
              <ModuleBrief
                name="Interest Rate Risk (IRRBB)"
                code="01"
                statusTone={statusTone(irr.data.metrics.eveStatus)}
                statusLabel={`EVE ${labelize(irr.data.metrics.eveStatus)}`}
                validations={{
                  total: irr.data.validations.length,
                  failed: irr.data.validations.filter((v) => !v.passed).length,
                }}
                run={byModule.get('irr')}
                computedAt={irr.data.live?.computedAt}
                rows={[
                  {
                    label: 'Worst ΔEVE / Tier 1',
                    hint: `Worst scenario: ${labelize(
                      irr.data.metrics.worstScenarioCode
                    )}`,
                    value: fmtPct(
                      num(irr.data.metrics.worstEveChangePctTier1),
                      2
                    ),
                    tone: statusTone(irr.data.metrics.eveStatus),
                    toneLabel: labelize(irr.data.metrics.eveStatus),
                  },
                  {
                    label: 'Worst ΔEVE',
                    value: fmtCurrency(num(irr.data.metrics.worstEveChangeGhs)),
                  },
                  {
                    label: 'Duration gap',
                    value: `${num(irr.data.metrics.durationGap).toFixed(2)} yrs`,
                  },
                  {
                    label: 'NII (base, 12m)',
                    value: fmtCurrency(num(irr.data.metrics.niiBaseGhs)),
                  },
                  {
                    label: 'EaR +200bp / −200bp',
                    value: `${fmtCurrency(
                      num(irr.data.metrics.earUp200Ghs)
                    )} / ${fmtCurrency(num(irr.data.metrics.earDown200Ghs))}`,
                  },
                ]}
              />
            )}
          </QueryBoundary>
        </BoardPage>

        <BoardPage>
          <QueryBoundary
            isLoading={fx.isLoading}
            error={fx.error}
            onRetry={() => fx.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {fx.data && (
              <ModuleBrief
                name="FX Risk"
                code="03"
                statusTone={statusTone(fx.data.metrics.nopStatus)}
                statusLabel={`NOP ${labelize(fx.data.metrics.nopStatus)}`}
                validations={{
                  total: fx.data.validations.length,
                  failed: fx.data.validations.filter((v) => !v.passed).length,
                }}
                run={byModule.get('fx')}
                computedAt={fx.data.live?.computedAt}
                rows={[
                  {
                    label: 'Net open position / Tier 1',
                    hint: `Aggregate limit ${fmtPct(
                      num(fx.data.metrics.nopAggregateLimitPct),
                      0
                    )}`,
                    value: fmtPct(num(fx.data.metrics.nopPctTier1), 2),
                    tone: statusTone(fx.data.metrics.nopStatus),
                    toneLabel: labelize(fx.data.metrics.nopStatus),
                  },
                  {
                    label: 'Net open position',
                    value: fmtCurrency(num(fx.data.metrics.nopGhs)),
                  },
                  {
                    label: `Largest single currency (${fx.data.metrics.singleCcyMaxCurrency})`,
                    hint: `Single-currency limit ${fmtPct(
                      num(fx.data.metrics.nopSingleLimitPct),
                      0
                    )}`,
                    value: fmtPct(num(fx.data.metrics.singleCcyMaxPct), 2),
                    tone: statusTone(fx.data.metrics.singleCcyStatus),
                    toneLabel: labelize(fx.data.metrics.singleCcyStatus),
                  },
                  {
                    label: 'Standalone VaR (total)',
                    value: fmtCurrency(
                      num(fx.data.metrics.standaloneVarTotalGhs)
                    ),
                  },
                  {
                    label: 'Stressed VaR',
                    value: fmtCurrency(num(fx.data.metrics.stressedVarGhs)),
                  },
                ]}
              />
            )}
          </QueryBoundary>
        </BoardPage>

        <BoardPage>
          <QueryBoundary
            isLoading={ftp.isLoading}
            error={ftp.error}
            onRetry={() => ftp.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {ftp.data && (
              <ModuleBrief
                name="Funds Transfer Pricing"
                code="05"
                statusTone={statusTone(ftp.data.metrics.nmdCoreStatus)}
                statusLabel={`Core NMD ${labelize(
                  ftp.data.metrics.nmdCoreStatus
                )}`}
                validations={{
                  total: ftp.data.validations.length,
                  failed: ftp.data.validations.filter((v) => !v.passed).length,
                }}
                run={byModule.get('ftp')}
                computedAt={ftp.data.live?.computedAt}
                rows={[
                  {
                    label: 'Portfolio NIM',
                    value: fmtPct(num(ftp.data.metrics.portfolioNimPct), 2),
                  },
                  {
                    label: 'Blended assigned FTP',
                    value: fmtPct(
                      num(ftp.data.metrics.blendedAssignedFtpPct),
                      2
                    ),
                  },
                  {
                    label: 'Core NMD share',
                    hint: `Band ${fmtPct(
                      num(ftp.data.metrics.nmdCoreMinPct),
                      0
                    )}–${fmtPct(num(ftp.data.metrics.nmdCoreMaxPct), 0)}`,
                    value: fmtPct(num(ftp.data.metrics.nmdCorePct), 2),
                    tone: statusTone(ftp.data.metrics.nmdCoreStatus),
                    toneLabel: labelize(ftp.data.metrics.nmdCoreStatus),
                  },
                  {
                    label: 'Products below minimum margin',
                    value: `${fmtNum(
                      ftp.data.metrics.productsBelowMinMargin
                    )} of ${fmtNum(ftp.data.metrics.totalProducts)}`,
                  },
                  {
                    label: 'Total funded balance',
                    value: fmtCurrency(num(ftp.data.metrics.totalBalanceGhs)),
                  },
                ]}
              />
            )}
          </QueryBoundary>
        </BoardPage>
      </div>
    </>
  );
}
