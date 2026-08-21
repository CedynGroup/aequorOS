'use client';

/**
 * Risk & Limit Monitor — the cross-module limit wall. Pulls every module
 * dashboard, extracts each limit whose numeric threshold exists in the
 * payload (never inventing one), and renders them as a grouped LimitBar wall
 * with breach/amber filters, plus every module's rule evaluations.
 */

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { BellRing, Gauge } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import SubTabs from '@/components/ui/SubTabs';
import ValidationList from '@/components/ui/ValidationList';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import { PageSkeleton } from '@/components/ui/QueryBoundary';
import { useBankContext, useModuleScope } from '@/components/shell/BankContext';
import {
  useSdiCapitalChecks,
  useSdiCapitalSummary,
  useSdiLargeExposures,
  useSdiLiquidityPosition,
} from '@/components/basel/sdiHooks';
import { isHrefVisible } from '@/lib/modules';
import {
  useBankAlerts,
  useCapitalDashboard,
  useFtpDashboard,
  useFxDashboard,
  useIrrDashboard,
  useLiquidityDashboard,
  useLiveSummary,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtRelative, statusTone } from '@/lib/api/values';
import LimitWall from '@/components/risk/LimitWall';
import {
  extractAllLimits,
  MODULE_HREFS,
  MODULE_LABELS,
  type LimitModule,
  type LimitRow,
} from '@/components/risk/limits';

type Filter = 'all' | 'breach' | 'amber';

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All limits' },
  { key: 'breach', label: 'Breaches' },
  { key: 'amber', label: 'Amber' },
];

export default function RiskLimitMonitorPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  // Scope the limit wall to the tenant's modules (docs/sdi.md §3.2): only fetch a
  // module's dashboard when it is in scope, so an SDI raises no 403s for FX/FTP and
  // shows no limit bars for engines it does not run. Liquidity + capital are in
  // every institution's set.
  const scope = useModuleScope();
  const isSdi = scope.institutionClass === 'sdi';
  const irrScoped = isHrefVisible('/irr/limits', scope);
  const fxScoped = isHrefVisible('/fx/limits', scope);
  const ftpScoped = isHrefVisible('/ftp/products', scope);

  const liquidity = useLiquidityDashboard(isSdi ? undefined : bankId);
  const capital = useCapitalDashboard(isSdi ? undefined : bankId);
  const irr = useIrrDashboard(irrScoped ? bankId : undefined);
  const fx = useFxDashboard(fxScoped ? bankId : undefined);
  const ftp = useFtpDashboard(ftpScoped ? bankId : undefined);
  const liveSummary = useLiveSummary(bankId);
  const alerts = useBankAlerts(bankId, 200);
  const liquidityRun = useRegulatoryRun(bankId, liquidity.data?.latestRunId ?? undefined);
  const sdiLiquidity = useSdiLiquidityPosition(isSdi ? bankId : undefined);
  const sdiCapital = useSdiCapitalSummary(isSdi ? bankId : undefined);
  const sdiChecks = useSdiCapitalChecks(isSdi ? bankId : undefined);
  const sdiExposures = useSdiLargeExposures(isSdi ? bankId : undefined);

  const [filter, setFilter] = useState<Filter>('all');
  const [tab, setTab] = useState('wall');

  const moduleQueries = [
    ['liquidity', liquidity],
    ['capital', capital],
    ['irr', irr],
    ['fx', fx],
    ['ftp', ftp],
  ] as const;

  const rows = useMemo<LimitRow[]>(() => {
    if (isSdi) {
      const status = (value: string): LimitRow['status'] =>
        value === 'below_minimum' || value === 'above_limit' || value === 'red'
          ? 'crit'
          : value === 'not_computable' || value === 'na'
          ? 'warn'
          : 'ok';
      const result: LimitRow[] = [];
      for (const ratio of sdiLiquidity.data?.ratios ?? []) {
        if (ratio.value_pct !== null) {
          result.push({
            module: 'liquidity',
            limit: ratio.label,
            value: Number(ratio.value_pct),
            threshold: Number(ratio.threshold_pct),
            direction: 'above',
            status: status(ratio.status),
            unit: '%',
            detail: 'LMTD Table 1',
          });
        }
      }
      for (const reserve of sdiLiquidity.data?.reserves ?? []) {
        if (reserve.value_pct !== null) {
          result.push({
            module: 'liquidity',
            limit: reserve.label,
            value: Number(reserve.value_pct),
            threshold: Number(reserve.threshold_pct),
            direction: 'above',
            status: status(reserve.status),
            unit: '%',
            detail: 'NBFI liquidity reserve',
          });
        }
      }
      const car = sdiCapital.data;
      if (car?.car_pct !== null && car) {
        result.push({
          module: 'capital',
          limit: 'Capital adequacy ratio',
          value: Number(car.car_pct),
          threshold: Number(car.car_min_pct),
          direction: 'above',
          status: status(car.status),
          unit: '%',
          detail: 'Act 930, Section 29',
        });
      }
      for (const check of sdiChecks.data?.checks ?? []) {
        if (check.actual_ghs !== null && check.required_ghs !== null) {
          result.push({
            module: 'capital',
            limit: check.check === 'paid_up_capital' ? 'Minimum paid-up capital' : 'Statutory reserve fund',
            value: Number(check.actual_ghs),
            threshold: Number(check.required_ghs),
            direction: 'above',
            status: check.compliant === true ? 'ok' : check.compliant === false ? 'crit' : 'warn',
            unit: 'GHS',
            detail: check.source_citation,
          });
        }
      }
      for (const exposure of sdiExposures.data?.exposures ?? []) {
        if (!exposure.exempt && exposure.pct_net_own_funds !== null) {
          result.push({
            module: 'exposures',
            limit: exposure.counterparty_name,
            value: Number(exposure.pct_net_own_funds),
            threshold: Number(exposure.large_exposure_limit_pct),
            direction: 'below',
            status: status(exposure.status),
            unit: '%',
            detail: exposure.connection === 'group' ? 'Connected group' : 'Single counterparty',
          });
        }
      }
      return result;
    }
    return extractAllLimits({
      liquidity: liquidity.data,
      liquidityRun: liquidityRun.data,
      capital: capital.data,
      irr: irr.data,
      fx: fx.data,
      ftp: ftp.data,
    });
  }, [
    capital.data,
    ftp.data,
    fx.data,
    irr.data,
    isSdi,
    liquidity.data,
    liquidityRun.data,
    sdiCapital.data,
    sdiChecks.data,
    sdiExposures.data,
    sdiLiquidity.data,
  ]);

  const isLoading = isSdi
    ? sdiLiquidity.isLoading || sdiCapital.isLoading || sdiChecks.isLoading || sdiExposures.isLoading
    : moduleQueries.some(([, query]) => query.isLoading);
  const unavailableModules = moduleQueries
    .filter(([, query]) => query.isError)
    .map(([module]) => module as LimitModule);

  const breaches = rows.filter((row) => row.status === 'crit');
  const ambers = rows.filter((row) => row.status === 'warn');
  const compliant = rows.filter((row) => row.status === 'ok');

  const filteredRows: LimitRow[] =
    filter === 'breach' ? breaches : filter === 'amber' ? ambers : rows;

  const liveModules = liveSummary.data?.modules ?? [];

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Command' }, { label: 'Risk & Limits' }]}
        title="Risk & Limit Monitor"
        subtitle="Bank-wide limit utilization and breach status — every threshold shown comes from the module payloads, never hardcoded in this page."
      />

      {isLoading ? (
        <PageSkeleton />
      ) : (
        <div className="px-8 py-6 space-y-6">
          {/* Summary KPIs */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            <KpiStat
              label="Limit breaches"
              value={breaches.length}
              status={breaches.length > 0 ? 'crit' : 'ok'}
              hint={`of ${rows.length} tracked limits`}
            />
            <KpiStat
              label="Amber (approaching)"
              value={ambers.length}
              status={ambers.length > 0 ? 'warn' : 'ok'}
              hint="within the early-warning zone"
            />
            <KpiStat
              label="Compliant"
              value={compliant.length}
              status="ok"
              hint="inside limit with headroom"
            />
            <KpiStat
              label="Open alerts"
              value={alerts.data?.total ?? '—'}
              status={(alerts.data?.total ?? 0) > 0 ? 'crit' : 'ok'}
              hint={
                <Link href="/alerts" className="text-action hover:underline">
                  Open the Alert Center →
                </Link>
              }
            />
          </div>

          {/* Live module status strip */}
          {liveModules.length > 0 && (
            <div className="card px-4 py-3 flex items-center gap-4 flex-wrap">
              <span className="text-micro font-medium text-slate uppercase tracking-wider">
                Live module status
              </span>
              {liveModules.map((module) => (
                <span key={module.module} className="inline-flex items-center gap-1.5">
                  <StatusPill tone={statusTone(module.status)}>
                    {module.module}
                  </StatusPill>
                  <span className="text-caption text-slate-light whitespace-nowrap">
                    {fmtRelative(module.computedAt)}
                  </span>
                </span>
              ))}
            </div>
          )}

          <SubTabs
            items={[
              { key: 'wall', label: `Limit wall (${rows.length})` },
              { key: 'checks', label: 'Validation checks' },
            ]}
            active={tab}
            onChange={setTab}
          />

          {tab === 'wall' && (
            <>
              <div className="flex items-center gap-2">
                {FILTERS.map((option) => {
                  const count =
                    option.key === 'breach'
                      ? breaches.length
                      : option.key === 'amber'
                      ? ambers.length
                      : rows.length;
                  const active = filter === option.key;
                  return (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => setFilter(option.key)}
                      className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                        active
                          ? 'bg-action-light text-action border-action/30'
                          : 'bg-surface text-slate border-border hover:text-navy'
                      }`}
                    >
                      {option.label} · {count}
                    </button>
                  );
                })}
              </div>

              {rows.length === 0 && unavailableModules.length === 0 ? (
                <EmptyState
                  Icon={Gauge}
                  title="No payload-backed limits to show"
                  description="No module dashboard exposed a numeric limit threshold for this period. Run scenarios in a module's Stress workbench, or check the validation checks tab for pass/fail rule evaluations."
                />
              ) : filteredRows.length === 0 ? (
                <EmptyState
                  Icon={BellRing}
                  title={filter === 'breach' ? 'No limit breaches' : 'Nothing in the amber zone'}
                  description="Every tracked limit is outside this filter — switch back to all limits for the full wall."
                />
              ) : (
                <LimitWall
                  rows={filteredRows}
                  unavailableModules={unavailableModules}
                  showEmptyModules={filter === 'all'}
                />
              )}
            </>
          )}

          {tab === 'checks' && isSdi && (
            <div className="space-y-6">
              <SectionCard title="SDI control coverage" subtitle="The limit wall uses the binding SDI measures available from the current canonical book.">
                <p className="text-body text-navy/85 leading-relaxed">
                  Liquidity controls cover LMTD Table 1 and reserve ratios. Capital controls cover Section 29 CAR, paid-up capital, statutory reserve fund, and connected-group exposures. Open pipeline findings continue to appear in the Alert Center.
                </p>
              </SectionCard>
            </div>
          )}

          {tab === 'checks' && !isSdi && (
            <div className="space-y-6">
              {moduleQueries.map(([module, query]) => {
                const validations = query.data?.validations ?? [];
                return (
                  <SectionCard
                    key={module}
                    title={MODULE_LABELS[module as LimitModule]}
                    subtitle="Rule evaluations from the module dashboard for this period"
                    actions={
                      <Link
                        href={MODULE_HREFS[module as LimitModule]}
                        className="text-caption font-medium text-action hover:underline"
                      >
                        Open module →
                      </Link>
                    }
                    noPadding
                  >
                    {query.isError ? (
                      <p className="px-5 py-4 text-body text-slate">
                        Module dashboard unavailable.
                      </p>
                    ) : (
                      <ValidationList validations={validations} />
                    )}
                  </SectionCard>
                );
              })}
            </div>
          )}
        </div>
      )}
    </>
  );
}
