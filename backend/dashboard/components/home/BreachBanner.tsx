'use client';

/**
 * Breach banner — the first thing a demo audience reads.
 *
 * Priority order, all from real signals:
 *   1. Open critical/high limit-breach alerts (live findings) → full-width
 *      critical strip listing the top three with module chips, link /alerts.
 *   2. No open alerts but modules reporting breach status for the effective
 *      period (e.g. inline-computed breaches before a pipeline refresh has
 *      minted findings) → critical strip naming those modules.
 *   3. Otherwise → slim success strip with the live/computed module count.
 *
 * Reads the polled alerts + live-summary views and the shared pulse-card
 * model (deduped with the pulse wall's own dashboard queries).
 */

import Link from 'next/link';
import { ArrowRight, CheckCircle2, ShieldAlert } from 'lucide-react';
import type {
  BankReportingPeriodRead,
  LiveModule,
} from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankAlerts, useLiveSummary } from '@/lib/api/hooks';
import { fmtRelative } from '@/lib/api/values';
import {
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
} from '@/components/live/moduleDisplay';
import { DEFAULT_MODULE_ORDER, usePulseCards } from './pulse';

export default function BreachBanner({
  bankId,
  period,
}: {
  bankId: string | undefined;
  period: BankReportingPeriodRead;
}) {
  const alerts = useBankAlerts(bankId);
  const live = useLiveSummary(bankId);
  const pulse = usePulseCards(bankId, period);

  if (alerts.isLoading || pulse.isLoading) {
    return (
      <div className="card px-5 py-3">
        <SkeletonLine width="40%" height={12} />
      </div>
    );
  }
  if (!alerts.data) return null;

  // 1. Open critical/high breach alerts from the live findings store.
  const openAlerts = alerts.data.items.filter(
    (item) => item.severity === 'critical' || item.severity === 'high'
  );
  if (openAlerts.length > 0) {
    const openCount =
      (alerts.data.bySeverity['critical'] ?? 0) +
      (alerts.data.bySeverity['high'] ?? 0);
    return (
      <div className="card border-l-4 border-l-critical bg-critical-light/40 px-5 py-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3 min-w-0">
            <ShieldAlert
              size={18}
              className="text-critical shrink-0 mt-0.5"
              aria-hidden
            />
            <div className="min-w-0">
              <p className="text-body font-semibold text-navy">
                {openCount} open limit breach{openCount === 1 ? '' : 'es'}{' '}
                requiring attention
              </p>
              <ul className="mt-2 space-y-1.5">
                {openAlerts.slice(0, 3).map((item) => (
                  <li
                    key={item.findingId}
                    className="flex items-start gap-2 min-w-0"
                  >
                    <StatusPill
                      tone={item.severity === 'critical' ? 'critical' : 'amber'}
                      className="shrink-0 mt-px"
                    >
                      {LIVE_MODULE_LABELS[item.module]}
                    </StatusPill>
                    <span className="text-body text-navy/85 leading-snug min-w-0">
                      {item.message}
                      <span className="text-caption text-slate whitespace-nowrap">
                        {' '}
                        · {fmtRelative(item.createdAt)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <Link
            href="/alerts"
            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            Review all alerts
            <ArrowRight size={13} aria-hidden />
          </Link>
        </div>
      </div>
    );
  }

  // 2. No alert findings yet, but the wall shows breach-status modules for
  //    this period (inline computations ahead of the live pipeline).
  const breached = DEFAULT_MODULE_ORDER.filter(
    (m) => pulse.cards[m].status === 'red'
  );
  if (breached.length > 0) {
    return (
      <div className="card border-l-4 border-l-critical bg-critical-light/40 px-5 py-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3 min-w-0">
            <ShieldAlert
              size={18}
              className="text-critical shrink-0 mt-0.5"
              aria-hidden
            />
            <div className="min-w-0">
              <p className="text-body font-semibold text-navy">
                {breached.length} module{breached.length === 1 ? '' : 's'}{' '}
                breaching limits for period {period.label}
              </p>
              <ul className="mt-2 space-y-1.5">
                {breached.slice(0, 3).map((module) => (
                  <BreachedModuleRow
                    key={module}
                    module={module}
                    metricLabel={pulse.cards[module].metricLabel}
                    value={pulse.cards[module].value}
                    unit={pulse.cards[module].unit}
                  />
                ))}
              </ul>
            </div>
          </div>
          <Link
            href={LIVE_MODULE_HREFS[breached[0]]}
            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            Open {LIVE_MODULE_LABELS[breached[0]]}
            <ArrowRight size={13} aria-hidden />
          </Link>
        </div>
      </div>
    );
  }

  // 3. Compliant.
  const liveModules = (live.data?.modules ?? []).filter(
    (m) => m.status !== 'na'
  ).length;
  const computedModules = DEFAULT_MODULE_ORDER.filter(
    (m) => pulse.cards[m].status !== 'na'
  ).length;
  const approaching = DEFAULT_MODULE_ORDER.filter(
    (m) => pulse.cards[m].status === 'amber'
  ).length;

  return (
    <div className="card border-l-4 border-l-success bg-success-light/40 px-5 py-2.5 flex items-center justify-between gap-3 flex-wrap">
      <p className="inline-flex items-center gap-2 text-body text-navy/85">
        <CheckCircle2 size={15} className="text-success shrink-0" aria-hidden />
        <span>
          All limits compliant
          {liveModules > 0 ? (
            <span className="text-slate"> · {liveModules} modules live</span>
          ) : computedModules > 0 ? (
            <span className="text-slate">
              {' '}
              · {computedModules} modules computed
            </span>
          ) : null}
          {approaching > 0 && (
            <span className="text-slate">
              {' '}
              · {approaching} approaching threshold
            </span>
          )}
          {live.data?.computedAt && (
            <span className="text-slate">
              {' '}
              · updated {fmtRelative(live.data.computedAt)}
            </span>
          )}
        </span>
      </p>
      <Link
        href="/alerts"
        className="text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1"
      >
        Alert history <ArrowRight size={12} aria-hidden />
      </Link>
    </div>
  );
}

function BreachedModuleRow({
  module,
  metricLabel,
  value,
  unit,
}: {
  module: LiveModule;
  metricLabel?: string;
  value?: string;
  unit?: string;
}) {
  return (
    <li className="flex items-start gap-2 min-w-0">
      <StatusPill tone="critical" className="shrink-0 mt-px">
        {LIVE_MODULE_LABELS[module]}
      </StatusPill>
      <Link
        href={LIVE_MODULE_HREFS[module]}
        className="text-body text-navy/85 leading-snug min-w-0 hover:text-action"
      >
        {metricLabel ?? 'Headline metric'}{' '}
        {value !== undefined && (
          <span className="font-mono tnum font-medium text-navy">
            {value}
            {unit ?? ''}
          </span>
        )}{' '}
        — breach status for this period
      </Link>
    </li>
  );
}
