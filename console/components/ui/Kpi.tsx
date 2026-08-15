import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowUpRight, TrendingDown, TrendingUp } from 'lucide-react';
import { StatusPill, type StatusTone } from './StatusPill';
import { Sparkline } from './Sparkline';
import { DeltaBadge } from './Delta';

export type KpiStatus = 'ok' | 'warn' | 'crit';

const edgeStyles: Record<KpiStatus, string> = {
  ok: 'inset 3px 0 0 rgb(var(--ok))',
  warn: 'inset 3px 0 0 rgb(var(--warn))',
  crit: 'inset 3px 0 0 rgb(var(--crit))',
};

/**
 * Adaptive value size: keep true metrics at the big numeral size, but shrink
 * long text values (dates, status phrases) so they fit the card rather than
 * overflow. This is why KPI values previously looked inconsistent and clipped —
 * a 3-char number and a 22-char phrase were both rendered at the full size.
 */
function valueSizeClass(value: string | number): string {
  const len = String(value).length;
  if (len <= 8) return 'text-kpi'; // 120 · 8 · 12.4%
  if (len <= 13) return 'text-h2'; // 2026-06-30
  return 'text-h3'; // "accepted with warnings"
}

/**
 * Dense KPI stat for module dashboards: micro label, big tabular-numeral
 * value, optional unit, delta vs the prior period, a sparkline slot, and a
 * status edge-glow on the left border.
 */
export function KpiStat({
  label,
  value,
  unit,
  delta,
  deltaSuffix = ' pts',
  deltaDecimals = 1,
  invertDelta = false,
  status,
  sparkline,
  hint,
  className = '',
}: {
  label: string;
  value: string | number;
  unit?: string;
  delta?: number;
  deltaSuffix?: string;
  deltaDecimals?: number;
  invertDelta?: boolean;
  status?: KpiStatus;
  sparkline?: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`card flex min-w-0 flex-col gap-1.5 px-4 py-3.5 ${className}`}
      style={status ? { boxShadow: edgeStyles[status] } : undefined}
    >
      <p className="text-micro font-medium uppercase tracking-wider text-slate">{label}</p>

      <div className="flex items-end justify-between gap-3">
        <div className="flex min-w-0 items-baseline gap-1">
          <span
            className={`min-w-0 truncate font-mono ${valueSizeClass(value)} text-navy tnum`}
            title={typeof value === 'number' ? String(value) : value}
          >
            {typeof value === 'number' ? String(value) : value}
          </span>
          {unit && <span className="shrink-0 text-body text-slate">{unit}</span>}
        </div>
        {sparkline && <div className="shrink-0 pb-1">{sparkline}</div>}
      </div>

      {(delta !== undefined || hint) && (
        <div className="flex min-w-0 items-center justify-between gap-2">
          {delta !== undefined ? (
            <DeltaBadge
              value={delta}
              suffix={deltaSuffix}
              decimals={deltaDecimals}
              invert={invertDelta}
            />
          ) : (
            <span />
          )}
          {hint && <span className="text-caption text-slate">{hint}</span>}
        </div>
      )}
    </div>
  );
}

type Variant = 'default' | 'ratio' | 'compact';

/**
 * Larger, optionally-linked KPI card: display-scale value with prefix/suffix,
 * threshold caption, status pill, trend delta, and a sparkline. Use KpiStat
 * for dense grids; KPICard for hero figures.
 */
export function KPICard({
  label,
  value,
  suffix,
  prefix,
  threshold,
  thresholdLabel,
  status,
  delta,
  deltaSuffix = ' pts',
  sparkline,
  href,
  variant = 'default',
  decimals = 1,
  footer,
}: {
  label: string;
  value: number;
  suffix?: string;
  prefix?: string;
  threshold?: number;
  thresholdLabel?: string;
  status?: StatusTone;
  delta?: number;
  deltaSuffix?: string;
  sparkline?: number[];
  href?: string;
  variant?: Variant;
  decimals?: number;
  footer?: ReactNode;
}) {
  const formatted = value.toFixed(decimals);
  const deltaPositive = (delta ?? 0) >= 0;
  const TrendIcon = deltaPositive ? TrendingUp : TrendingDown;
  const trendColor = deltaPositive ? 'text-success' : 'text-critical';

  const inner = (
    <div className="card group relative flex h-full flex-col gap-4 p-5 transition-colors hover:border-action/40">
      <div className="flex items-start justify-between gap-3">
        <p className="text-caption font-medium uppercase tracking-wider text-slate">{label}</p>
        {href && (
          <ArrowUpRight
            size={14}
            className="shrink-0 text-slate transition-colors group-hover:text-action"
            aria-hidden
          />
        )}
      </div>

      <div>
        <div className="flex items-baseline gap-1">
          {prefix && <span className="text-h3 text-slate">{prefix}</span>}
          <span className="font-mono text-display text-navy tabular-nums">{formatted}</span>
          {suffix && <span className="ml-0.5 text-h2 text-navy">{suffix}</span>}
        </div>

        {(threshold !== undefined || thresholdLabel) && (
          <p className="mt-1 text-caption text-slate">
            {thresholdLabel ??
              (threshold !== undefined ? `Threshold ${threshold}${suffix ?? ''}` : null)}
          </p>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {status && <StatusPill tone={status} />}
          {delta !== undefined && variant !== 'compact' && (
            <span className={`inline-flex items-center gap-1 text-caption font-medium ${trendColor}`}>
              <TrendIcon size={12} aria-hidden />
              <span className="font-mono tabular-nums">
                {deltaPositive ? '+' : ''}
                {delta.toFixed(decimals)}
                {deltaSuffix}
              </span>
            </span>
          )}
        </div>
        {sparkline && sparkline.length > 0 && (
          <Sparkline
            data={sparkline}
            color={
              status === 'breach' || status === 'critical'
                ? 'rgb(var(--crit))'
                : status === 'approaching' || status === 'amber'
                ? 'rgb(var(--warn))'
                : 'rgb(var(--ok))'
            }
            width={68}
            height={24}
          />
        )}
      </div>
      {footer && (
        <div className="-mx-5 mt-1 border-t border-border-light px-5 pt-3 text-caption text-slate">
          {footer}
        </div>
      )}
    </div>
  );

  return href ? (
    <Link href={href} className="block">
      {inner}
    </Link>
  ) : (
    inner
  );
}
