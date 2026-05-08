import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowUpRight, TrendingUp, TrendingDown } from 'lucide-react';
import StatusPill, { type StatusTone } from './StatusPill';
import Sparkline from './Sparkline';

type Variant = 'default' | 'ratio' | 'compact';

export default function KPICard({
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
    <div className="card p-5 h-full flex flex-col gap-4 relative group hover:border-action/40 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <p className="text-caption font-medium text-slate uppercase tracking-wider">
          {label}
        </p>
        {href && (
          <ArrowUpRight
            size={14}
            className="text-slate group-hover:text-action transition-colors shrink-0"
            aria-hidden
          />
        )}
      </div>

      <div>
        <div className="flex items-baseline gap-1">
          {prefix && (
            <span className="text-h3 text-slate">{prefix}</span>
          )}
          <span className="font-mono text-display text-navy tabular-nums">
            {formatted}
          </span>
          {suffix && (
            <span className="text-h2 text-navy ml-0.5">{suffix}</span>
          )}
        </div>

        {(threshold !== undefined || thresholdLabel) && (
          <p className="mt-1 text-caption text-slate">
            {thresholdLabel ??
              (threshold !== undefined
                ? `Threshold ${threshold}${suffix ?? ''}`
                : null)}
          </p>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {status && <StatusPill tone={status} />}
          {delta !== undefined && variant !== 'compact' && (
            <span
              className={`inline-flex items-center gap-1 text-caption font-medium ${trendColor}`}
            >
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
                ? '#B3261E'
                : status === 'approaching' || status === 'amber'
                ? '#C97C00'
                : '#0E8A4F'
            }
            width={68}
            height={24}
          />
        )}
      </div>
      {footer && (
        <div className="border-t border-border-light -mx-5 px-5 pt-3 mt-1 text-caption text-slate">
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
