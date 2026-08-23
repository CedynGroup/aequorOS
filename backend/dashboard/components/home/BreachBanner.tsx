'use client';

/**
 * Breach banner — the first thing a demo audience reads.
 *
 * Priority order, all from real signals:
 *   1. Open critical/high limit-breach alerts (live findings) → full-width
 *      critical strip listing the top three with module chips, link /alerts.
 *   2. No open alerts but modules reporting breach status for the effective
 *      period (e.g. inline-computed breaches before a pipeline refresh has
 *      stored findings) → critical strip naming those modules.
 *   3. No breach AND nothing computable → indeterminate strip. NOT a pass.
 *   4. No breach, some modules computed → compliance is affirmed only for the
 *      modules that actually returned a status, and any module that did not is
 *      named on the strip.
 *
 * FAIL CLOSED. This banner is the first "are we safe?" signal in the product,
 * so silence must never read as safety. Previously only `status === 'red'`
 * counted, and a tenant whose every in-scope module was `'na'` fell through to
 * a green "All limits compliant" — a compliance affirmation built on zero
 * measurements.
 *
 * Reads the polled alerts + live-summary views and the shared pulse-card
 * model (deduped with the pulse wall's own dashboard queries).
 */

import Link from 'next/link';
import { ArrowRight, CheckCircle2, HelpCircle, ShieldAlert } from 'lucide-react';
import type { LiveModule } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankAlerts, useLiveSummary } from '@/lib/api/hooks';
import { fmtRelative, moduleComplianceVerdict } from '@/lib/api/values';
import {
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
} from '@/components/live/moduleDisplay';
import { useModuleScope } from '@/components/shell/BankContext';
import { isHrefVisible } from '@/lib/modules';
import { DEFAULT_MODULE_ORDER, usePulseCards } from './pulse';

export default function BreachBanner({
  bankId,
  hasData = true,
}: {
  bankId: string | undefined;
  /** False when no period has computed data yet — suppresses the module fetches
   * behind the pulse model that would 409 on a fresh tenant. */
  hasData?: boolean;
}) {
  const alerts = useBankAlerts(bankId);
  const live = useLiveSummary(bankId);
  const pulse = usePulseCards(bankId, hasData);
  // Scope the census exactly as the pulse wall does (docs/sdi.md §3.2): an SDI
  // does not run the FX/FTP engines, so those cards are 'na' BY DESIGN and must
  // not be counted as "not computable" — that would cry wolf on every SDI.
  const scope = useModuleScope();
  const inScope = DEFAULT_MODULE_ORDER.filter((module) =>
    isHrefVisible(LIVE_MODULE_HREFS[module], scope)
  );

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
                        · open since {fmtRelative(item.createdAt)}
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
  const breached = inScope.filter((m) => pulse.cards[m].status === 'red');
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
                breaching live limits
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

  // 3./4. No breach found — but "no breach found" only means something for the
  //       modules that were actually measured.
  const liveModules = (live.data?.modules ?? []).filter(
    (m) => m.status !== 'na'
  ).length;
  const computed = inScope.filter((m) => pulse.cards[m].status !== 'na');
  const notComputable = inScope.filter((m) => pulse.cards[m].status === 'na');
  const approaching = inScope.filter(
    (m) => pulse.cards[m].status === 'amber'
  ).length;
  // The single fail-closed rule, shared with its unit test
  // (lib/api/values.ts::moduleComplianceVerdict). Branch 2 above has already
  // returned on 'breach'; what remains is compliant / partial / not_assessed.
  const verdict = moduleComplianceVerdict(
    inScope.map((m) => pulse.cards[m].status)
  );
  const updatedAt = live.data?.computedAt ? (
    <span className="text-slate"> · updated {fmtRelative(live.data.computedAt)}</span>
  ) : null;

  // 3. Nothing is computable: no limit was evaluated, so no compliance claim
  //    can be made. Neutral strip, never the green one.
  if (verdict === 'not_assessed') {
    return (
      <div className="card border-l-4 border-l-warning bg-warning-light/30 px-5 py-3 flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-3 min-w-0">
          <HelpCircle
            size={16}
            className="text-warning shrink-0 mt-0.5"
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-body font-semibold text-navy">
              Limit compliance not assessed
            </p>
            <p className="mt-1 text-caption text-slate leading-snug">
              {inScope.length === 0
                ? 'No regulatory module is in scope for this institution yet.'
                : `None of the ${inScope.length} in-scope modules returned a computable position for this period, so nothing has been checked against a limit — this is not a clean result.`}
            </p>
          </div>
        </div>
        <Link
          href="/data-engine"
          className="shrink-0 text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1"
        >
          Open the Data Engine <ArrowRight size={12} aria-hidden />
        </Link>
      </div>
    );
  }

  // 4. Partial coverage: affirm compliance ONLY over what was measured, and
  //    name what was not.
  if (verdict === 'partial') {
    return (
      <div className="card border-l-4 border-l-warning bg-warning-light/30 px-5 py-3 flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-3 min-w-0">
          <HelpCircle
            size={16}
            className="text-warning shrink-0 mt-0.5"
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-body font-semibold text-navy">
              No breach in {computed.length} of {inScope.length} modules ·{' '}
              {notComputable.length} not assessed
            </p>
            <p className="mt-1 text-caption text-slate leading-snug">
              Not computable:{' '}
              {notComputable.map((m) => LIVE_MODULE_LABELS[m]).join(', ')}. Their limits
              have not been checked for this period.
              {approaching > 0 ? ` ${approaching} of the measured modules ${approaching === 1 ? 'is' : 'are'} approaching a threshold.` : ''}
            </p>
          </div>
        </div>
        <Link
          href={LIVE_MODULE_HREFS[notComputable[0]]}
          className="shrink-0 text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1"
        >
          Open {LIVE_MODULE_LABELS[notComputable[0]]} <ArrowRight size={12} aria-hidden />
        </Link>
      </div>
    );
  }

  // 5. Every in-scope module measured, none breaching.
  return (
    <div className="card border-l-4 border-l-success bg-success-light/40 px-5 py-2.5 flex items-center justify-between gap-3 flex-wrap">
      <p className="inline-flex items-center gap-2 text-body text-navy/85">
        <CheckCircle2 size={15} className="text-success shrink-0" aria-hidden />
        <span>
          All limits compliant
          {liveModules > 0 ? (
            <span className="text-slate"> · {liveModules} modules live</span>
          ) : (
            <span className="text-slate"> · {computed.length} modules computed</span>
          )}
          {approaching > 0 && (
            <span className="text-slate">
              {' '}
              · {approaching} approaching threshold
            </span>
          )}
          {updatedAt}
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
