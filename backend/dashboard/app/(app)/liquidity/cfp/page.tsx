'use client';

import Link from 'next/link';
import { LifeBuoy, Zap } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import IllustrativeBadge from '@/components/liquidity/IllustrativeBadge';
import { runComputedAt, runThresholds } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import { useLiquidityDashboard, useRegulatoryRun } from '@/lib/api/hooks';
import { fmtDateUTC, num, statusTone } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

type IndicatorRow = {
  indicator: string;
  basis: string;
  current: string;
  watchTrigger: string;
  actionTrigger: string;
  tone: StatusTone;
  toneLabel?: string;
};

const indicatorColumns: Column<IndicatorRow>[] = [
  {
    key: 'indicator',
    header: 'Early-warning indicator',
    width: '30%',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.indicator}</p>
        <p className="text-caption text-slate">{r.basis}</p>
      </div>
    ),
  },
  {
    key: 'current',
    header: 'Current',
    numeric: true,
    render: (r) => r.current,
  },
  {
    key: 'watch',
    header: 'Watch trigger',
    numeric: true,
    render: (r) => r.watchTrigger,
  },
  {
    key: 'action',
    header: 'Action trigger',
    numeric: true,
    render: (r) => r.actionTrigger,
  },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) => <StatusPill tone={r.tone}>{r.toneLabel}</StatusPill>,
  },
];

/** Escalation-playbook framework content — illustrative, not engine output. */
const PLAYBOOK_STAGES = [
  {
    stage: 'Stage 0 — Business as usual',
    trigger: 'All early-warning indicators green.',
    actions:
      'Standard daily liquidity monitoring; monthly ALCO review of the indicator table above.',
    owner: 'Treasury middle office',
  },
  {
    stage: 'Stage 1 — Heightened monitoring',
    trigger:
      'Any indicator amber (e.g. LCR between the red floor and the BoG minimum) or a stress scenario projecting a breach.',
    actions:
      'Move to daily ALCO reporting, refresh the funding-source inventory, pre-position collateral, restrict discretionary asset growth.',
    owner: 'Head of Treasury',
  },
  {
    stage: 'Stage 2 — CFP activation',
    trigger:
      'Any indicator red (LCR or NSFR below the BoG minimum) on an actual reporting basis.',
    actions:
      'Convene the crisis funding committee, draw contingent funding lines, execute the asset-liquidation ladder starting with Level 1 HQLA, daily cash-flow forecasting at desk level.',
    owner: 'CFO / ALCO chair',
  },
  {
    stage: 'Stage 3 — Regulatory notification',
    trigger:
      'A confirmed regulatory-minimum breach or CFP measures insufficient within the survival horizon.',
    actions:
      'Notify Bank of Ghana Banking Supervision, file the remediation plan, activate recovery-plan funding options.',
    owner: 'CEO / Board risk committee',
  },
] as const;

export default function ContingencyFundingPlan() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;
  const run = latestRun.data;
  const thresholds = runThresholds(run);
  const lcrMin = thresholds['lcr_min'] ?? 100;
  const lcrRedFloor = thresholds['lcr_amber_floor'] ?? 90;
  const nsfrMin = thresholds['nsfr_min'] ?? 100;
  const nsfrRedFloor = thresholds['nsfr_amber_floor'] ?? nsfrMin;

  const lcr = num(data?.metrics.lcrPct);
  const nsfr = num(data?.metrics.nsfrPct);

  const failedErrors = (data?.validations ?? []).filter(
    (v) => !v.passed && v.severity === 'error'
  );
  const failedWarnings = (data?.validations ?? []).filter(
    (v) => !v.passed && v.severity === 'warning'
  );
  const allLevel1 = data?.validations.find(
    (v) => v.ruleCode === 'hqla_all_level1'
  );

  const indicatorRows: IndicatorRow[] = data
    ? [
        {
          indicator: 'Liquidity Coverage Ratio',
          basis: 'Reported LCR for the current period',
          current: fmtPct(lcr, 2),
          watchTrigger: `< ${lcrMin.toFixed(0)}%`,
          actionTrigger: `< ${lcrRedFloor.toFixed(0)}%`,
          tone: statusTone(data.metrics.lcrStatus),
        },
        {
          indicator: 'Net Stable Funding Ratio',
          basis: 'Reported NSFR for the current period',
          current: fmtPct(nsfr, 2),
          watchTrigger: `< ${nsfrMin.toFixed(0)}%`,
          actionTrigger:
            nsfrRedFloor < nsfrMin
              ? `< ${nsfrRedFloor.toFixed(0)}%`
              : `< ${nsfrMin.toFixed(0)}%`,
          tone: statusTone(data.metrics.nsfrStatus),
        },
        {
          indicator: 'Hard validation breaches',
          basis: 'Failed error-severity regulatory rules',
          current: String(failedErrors.length),
          watchTrigger: '≥ 1 warning',
          actionTrigger: '≥ 1 error',
          tone:
            failedErrors.length > 0
              ? 'critical'
              : failedWarnings.length > 0
              ? 'amber'
              : 'success',
          toneLabel:
            failedErrors.length > 0
              ? 'Action'
              : failedWarnings.length > 0
              ? 'Watch'
              : 'Clear',
        },
        {
          indicator: 'LCR amber zone',
          basis: `Between the ${lcrRedFloor.toFixed(0)}% red floor and the ${lcrMin.toFixed(0)}% minimum`,
          current: lcr >= lcrMin ? 'Outside' : lcr >= lcrRedFloor ? 'Inside' : 'Below floor',
          watchTrigger: 'Inside zone',
          actionTrigger: 'Below floor',
          tone:
            lcr >= lcrMin ? 'success' : lcr >= lcrRedFloor ? 'amber' : 'critical',
          toneLabel:
            lcr >= lcrMin ? 'Clear' : lcr >= lcrRedFloor ? 'Watch' : 'Action',
        },
        {
          indicator: 'HQLA quality',
          basis: 'Share of buffer held in Level 1 assets',
          current: allLevel1?.passed ? 'All Level 1' : 'Includes < Level 1',
          watchTrigger: 'Any < Level 1',
          actionTrigger: 'Sustained < Level 1 reliance',
          tone: allLevel1?.passed ? 'success' : 'amber',
          toneLabel: allLevel1?.passed ? 'Clear' : 'Watch',
        },
      ]
    : [];

  const activeStage =
    failedErrors.length > 0 ||
    data?.metrics.lcrStatus === 'red' ||
    data?.metrics.nsfrStatus === 'red'
      ? 2
      : data?.metrics.lcrStatus === 'amber' ||
        data?.metrics.nsfrStatus === 'amber' ||
        failedWarnings.length > 0
      ? 1
      : 0;

  const computedAt = runComputedAt(run);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'CFP' },
        ]}
        title="Contingency Funding Plan"
        subtitle="Early-warning indicators on live regulatory data · ILAAP escalation framework"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={run ? <RunBadge run={run} /> : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
            {/* Posture summary */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <KpiStat
                label="CFP posture"
                value={`Stage ${activeStage}`}
                status={activeStage >= 2 ? 'crit' : activeStage === 1 ? 'warn' : 'ok'}
                hint={
                  activeStage === 0
                    ? 'Business as usual'
                    : activeStage === 1
                    ? 'Heightened monitoring'
                    : 'CFP activation criteria met'
                }
              />
              <KpiStat
                label="LCR"
                value={fmtPct(lcr, 2)}
                status={
                  data.metrics.lcrStatus === 'red'
                    ? 'crit'
                    : data.metrics.lcrStatus === 'amber'
                    ? 'warn'
                    : 'ok'
                }
                hint={`Floors ${lcrRedFloor.toFixed(0)}% / ${lcrMin.toFixed(0)}%`}
              />
              <KpiStat
                label="NSFR"
                value={fmtPct(nsfr, 2)}
                status={
                  data.metrics.nsfrStatus === 'red'
                    ? 'crit'
                    : data.metrics.nsfrStatus === 'amber'
                    ? 'warn'
                    : 'ok'
                }
                hint={`BoG minimum ${nsfrMin.toFixed(0)}%`}
              />
              <KpiStat
                label="Failed validations"
                value={`${failedErrors.length + failedWarnings.length}`}
                status={
                  failedErrors.length > 0
                    ? 'crit'
                    : failedWarnings.length > 0
                    ? 'warn'
                    : 'ok'
                }
                hint={`${failedErrors.length} error · ${failedWarnings.length} warning`}
              />
            </div>

            {/* Early-warning indicators — REAL data */}
            <SectionCard
              title="Early-warning indicators"
              subtitle="Current values from the regulatory engine, evaluated against the active BoG thresholds"
              noPadding
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={
                <span>
                  {data.stored
                    ? 'Stored baseline run'
                    : 'Live computation — run baseline on the Cockpit to persist'}
                  {' · '}
                  <Link href="/liquidity/stress" className="text-action hover:underline">
                    View stressed ratios
                  </Link>
                </span>
              }
            >
              <DataTable columns={indicatorColumns} rows={indicatorRows} />
            </SectionCard>

            {/* Escalation playbook — framework content */}
            <SectionCard
              title="Escalation playbook"
              subtitle="Staged response framework mapped to the indicator states above"
              actions={<IllustrativeBadge />}
              noPadding
              footer={
                <span>
                  Playbook stages, actions, and owners are framework
                  illustrations — adopt and calibrate through ALCO governance
                  before operational use. Indicator states driving the current
                  stage are live data.
                </span>
              }
            >
              <ul className="divide-y divide-border-light">
                {PLAYBOOK_STAGES.map((stage, i) => {
                  const isActive = i === activeStage;
                  return (
                    <li
                      key={stage.stage}
                      className={`px-5 py-4 flex items-start gap-4 ${
                        isActive ? 'bg-warning-light/30' : ''
                      }`}
                    >
                      <span
                        className={`mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-full shrink-0 ${
                          isActive
                            ? 'bg-warning text-white'
                            : 'bg-surface text-slate'
                        }`}
                        aria-hidden
                      >
                        {i === 3 ? <LifeBuoy size={15} /> : i === 2 ? <Zap size={15} /> : i}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <p className="text-body font-medium text-navy">
                            {stage.stage}
                          </p>
                          {isActive && (
                            <StatusPill tone={i >= 2 ? 'critical' : i === 1 ? 'amber' : 'success'}>
                              Current posture
                            </StatusPill>
                          )}
                        </div>
                        <p className="mt-1 text-caption text-slate leading-relaxed">
                          <span className="font-medium text-navy/80">Trigger:</span>{' '}
                          {stage.trigger}
                        </p>
                        <p className="mt-1 text-caption text-slate leading-relaxed">
                          <span className="font-medium text-navy/80">Actions:</span>{' '}
                          {stage.actions}
                        </p>
                      </div>
                      <span className="shrink-0 text-caption text-slate whitespace-nowrap">
                        {stage.owner}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </SectionCard>

            {/* Governance note */}
            <SectionCard
              title="Governance"
              subtitle="How this page relates to the ILAAP"
              actions={<IllustrativeBadge />}
            >
              <div className="text-body text-navy/85 leading-relaxed space-y-3">
                <p>
                  The CFP is the operational arm of the ILAAP: early-warning
                  indicators are monitored on every reporting period, stress
                  results on the Stress tab test the plan&apos;s adequacy, and the
                  BSD-3 submission evidences the reported position. Indicator
                  thresholds shown here are the active BoG parameter set
                  snapshotted into the latest baseline run.
                </p>
                <p>
                  Review cadence, committee composition, and funding-source
                  inventories are institution-specific and must be maintained
                  in the board-approved CFP document.
                </p>
              </div>
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
