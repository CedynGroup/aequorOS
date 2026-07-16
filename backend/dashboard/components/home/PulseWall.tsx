'use client';

/**
 * Six-module pulse wall — the Command Center centerpiece.
 *
 * One card per regulatory module (Liquidity, Basel capital, IRRBB, FX, FTP,
 * Forecasting), built by the shared `usePulseCards` model: headline metric
 * for the effective period, traffic-light status, real period-over-period
 * delta and sparkline from the dashboard trend series, and when the live
 * figure was computed. Cards are links: focusable, Enter navigates.
 */

import Link from 'next/link';
import type {
  BankReportingPeriodRead,
  LiveModule,
} from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import DeltaBadge from '@/components/ui/DeltaBadge';
import Sparkline from '@/components/ui/Sparkline';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { isApiError } from '@/lib/api/client';
import { fmtRelative, statusTone } from '@/lib/api/values';
import {
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
} from '@/components/live/moduleDisplay';
import type { ModuleOrder } from './RoleLens';
import {
  DEFAULT_MODULE_ORDER,
  STATUS_RANK,
  usePulseCards,
  type PulseCardModel,
  type Traffic,
} from './pulse';

const EDGE_STYLE: Record<Traffic, string> = {
  green: 'inset 2px 0 0 rgb(var(--ok))',
  amber: 'inset 2px 0 0 rgb(var(--warn))',
  red: 'inset 2px 0 0 rgb(var(--crit))',
};

const SPARK_COLOR: Record<Traffic | 'na', string> = {
  green: 'rgb(var(--ok))',
  amber: 'rgb(var(--warn))',
  red: 'rgb(var(--crit))',
  na: 'rgb(var(--line-strong))',
};

export default function PulseWall({
  bankId,
  period,
  moduleOrder,
}: {
  bankId: string | undefined;
  period: BankReportingPeriodRead;
  moduleOrder: ModuleOrder;
}) {
  const { cards } = usePulseCards(bankId, period);
  const order = resolveOrder(moduleOrder, cards);

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-3">
        <h2 className="text-h3 text-navy">Module pulse</h2>
        <p className="text-caption text-slate">
          Six regulatory engines · period {period.label}
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {order.map((module) => (
          <PulseCard key={module} card={cards[module]} />
        ))}
      </div>
    </div>
  );
}

function resolveOrder(
  moduleOrder: ModuleOrder,
  cards: Record<LiveModule, PulseCardModel>
): LiveModule[] {
  if (moduleOrder !== 'severity') return moduleOrder;
  // Risk lens: worst status first, stable on the default order.
  return [...DEFAULT_MODULE_ORDER].sort(
    (a, b) => STATUS_RANK[cards[a].status] - STATUS_RANK[cards[b].status]
  );
}

function PulseCard({ card }: { card: PulseCardModel }) {
  const label = LIVE_MODULE_LABELS[card.module];
  const href = LIVE_MODULE_HREFS[card.module];

  if (card.isLoading) {
    return (
      <div className="card px-4 py-3.5 space-y-3" aria-busy="true">
        <SkeletonLine width="45%" height={10} />
        <SkeletonLine width="60%" height={26} />
        <SkeletonLine width="80%" height={10} />
      </div>
    );
  }

  const pill =
    card.pill ??
    (card.status !== 'na'
      ? { tone: statusTone(card.status), label: pillLabel(card.status) }
      : undefined);

  const edge =
    card.status !== 'na' ? { boxShadow: EDGE_STYLE[card.status] } : undefined;

  return (
    <Link
      href={href}
      className="card block px-4 py-3.5 min-w-0 transition-colors hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      style={edge}
      aria-label={`${label} — open module`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption font-semibold text-navy truncate">{label}</p>
        {pill && (
          <StatusPill tone={pill.tone} className="shrink-0">
            {pill.label}
          </StatusPill>
        )}
      </div>

      {card.error ? (
        <p className="mt-2 text-caption text-slate leading-relaxed">
          {isApiError(card.error)
            ? card.error.message
            : 'Metrics unavailable for this period.'}
        </p>
      ) : (
        <>
          <p className="mt-2 text-micro font-medium text-slate uppercase tracking-wider truncate">
            {card.metricLabel ?? ' '}
          </p>
          <div className="mt-0.5 flex items-end justify-between gap-3">
            <div className="flex items-baseline gap-1 min-w-0">
              <span className="font-mono text-kpi text-navy tnum truncate">
                {card.value ?? '—'}
              </span>
              {card.unit && (
                <span className="text-body text-slate shrink-0">
                  {card.unit}
                </span>
              )}
            </div>
            {card.spark && (
              <div className="shrink-0 pb-1">
                <Sparkline
                  data={card.spark}
                  color={SPARK_COLOR[card.status]}
                  width={72}
                  height={22}
                />
              </div>
            )}
          </div>
          {card.hint && (
            <p className="mt-1 text-caption text-slate truncate">{card.hint}</p>
          )}
          <div className="mt-1.5 flex items-center justify-between gap-2 min-w-0">
            {card.delta !== undefined ? (
              <DeltaBadge
                value={card.delta}
                suffix=" pts"
                decimals={2}
                invert={card.invertDelta}
              />
            ) : (
              <span className="text-caption text-slate-light">
                vs prior period —
              </span>
            )}
            <span className="text-caption text-slate truncate">
              {card.computedAt
                ? `computed ${fmtRelative(card.computedAt)}`
                : (card.basisNote ?? '')}
            </span>
          </div>
        </>
      )}
    </Link>
  );
}

function pillLabel(status: Traffic): string {
  if (status === 'green') return 'Compliant';
  if (status === 'amber') return 'Approaching';
  return 'Breach';
}
