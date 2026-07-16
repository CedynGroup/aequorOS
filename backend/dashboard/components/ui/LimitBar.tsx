import type { ReactNode } from 'react';

export type LimitDirection = 'below' | 'above';

type LimitStatus = 'ok' | 'warn' | 'crit';

const barColor: Record<LimitStatus, string> = {
  ok: 'rgb(var(--ok))',
  warn: 'rgb(var(--warn))',
  crit: 'rgb(var(--crit))',
};

const valueTextColor: Record<LimitStatus, string> = {
  ok: 'text-success',
  warn: 'text-warning',
  crit: 'text-critical',
};

function defaultFormat(v: number): string {
  return Number.isInteger(v)
    ? v.toLocaleString('en-US')
    : v.toLocaleString('en-US', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
}

/**
 * Bullet-style horizontal limit gauge — the standard risk visual.
 *
 * Renders the measured value as a bar against a zoned track (safe / amber /
 * breach), with tick markers at the amber threshold and the hard limit, and a
 * headroom readout.
 *
 * direction='below' (default): ceiling limits (NOP/Tier1, ΔEVE/Tier1,
 * concentration) — compliant while the value stays under the limit.
 * direction='above': floor limits (LCR, NSFR, CAR) — compliant while the
 * value stays above the limit.
 */
export default function LimitBar({
  label,
  value,
  limit,
  warnAt,
  max,
  direction = 'below',
  unit = '',
  format = defaultFormat,
  limitLabel = 'Limit',
  warnLabel = 'Amber',
  showHeadroom = true,
  meta,
  className = '',
}: {
  label?: ReactNode;
  /** Measured value. */
  value: number;
  /** Hard (red) limit. */
  limit: number;
  /** Amber threshold. Defaults to 80% of the way to the limit. */
  warnAt?: number;
  /** Scale maximum; defaults to a sensible margin past value and limit. */
  max?: number;
  direction?: LimitDirection;
  unit?: string;
  format?: (v: number) => string;
  limitLabel?: string;
  warnLabel?: string;
  showHeadroom?: boolean;
  /** Optional right-hand metadata slot under the bar. */
  meta?: ReactNode;
  className?: string;
}) {
  const isBelow = direction === 'below';
  const amber = warnAt ?? (isBelow ? limit * 0.8 : limit * 1.2);

  const status: LimitStatus = isBelow
    ? value >= limit
      ? 'crit'
      : value >= amber
      ? 'warn'
      : 'ok'
    : value <= limit
    ? 'crit'
    : value <= amber
    ? 'warn'
    : 'ok';

  const scaleMax =
    max ?? Math.max(value, limit, amber) * (isBelow ? 1.15 : 1.25);
  const pct = (v: number) =>
    Math.max(0, Math.min(100, (v / scaleMax) * 100));

  const pctValue = pct(value);
  const pctLimit = pct(limit);
  const pctAmber = pct(amber);

  const headroom = isBelow ? limit - value : value - limit;
  const headroomPct = limit !== 0 ? (headroom / limit) * 100 : 0;

  return (
    <div className={`min-w-0 ${className}`}>
      {(label || unit) && (
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          {label && (
            <span className="text-caption font-medium text-navy truncate">
              {label}
            </span>
          )}
          <span
            className={`font-mono text-caption font-semibold tnum whitespace-nowrap ${valueTextColor[status]}`}
          >
            {format(value)}
            {unit}
          </span>
        </div>
      )}

      {/* Zoned track */}
      <div
        className="relative h-3 rounded-sm overflow-hidden"
        style={{ background: 'rgb(var(--surface-hover))' }}
        role="img"
        aria-label={`${format(value)}${unit} of ${format(limit)}${unit} ${limitLabel.toLowerCase()}`}
      >
        {isBelow ? (
          <>
            {/* amber zone: amber → limit */}
            <div
              className="absolute inset-y-0"
              style={{
                left: `${pctAmber}%`,
                width: `${Math.max(0, pctLimit - pctAmber)}%`,
                background: 'rgb(var(--warn-soft))',
              }}
              aria-hidden
            />
            {/* breach zone: past the limit */}
            <div
              className="absolute inset-y-0"
              style={{
                left: `${pctLimit}%`,
                right: 0,
                background: 'rgb(var(--crit-soft))',
              }}
              aria-hidden
            />
          </>
        ) : (
          <>
            {/* breach zone: below the floor */}
            <div
              className="absolute inset-y-0 left-0"
              style={{
                width: `${pctLimit}%`,
                background: 'rgb(var(--crit-soft))',
              }}
              aria-hidden
            />
            {/* amber zone: floor → amber */}
            <div
              className="absolute inset-y-0"
              style={{
                left: `${pctLimit}%`,
                width: `${Math.max(0, pctAmber - pctLimit)}%`,
                background: 'rgb(var(--warn-soft))',
              }}
              aria-hidden
            />
          </>
        )}

        {/* Measured value — bullet bar, thinner than the track */}
        <div
          className="absolute left-0 top-1/2 -translate-y-1/2 h-1.5 rounded-r-sm transition-[width] duration-300"
          style={{ width: `${pctValue}%`, background: barColor[status] }}
          aria-hidden
        />

        {/* Amber threshold tick */}
        <div
          className="absolute inset-y-0 w-px"
          style={{ left: `${pctAmber}%`, background: 'rgb(var(--warn))' }}
          title={`${warnLabel} ${format(amber)}${unit}`}
          aria-hidden
        />
        {/* Hard limit tick */}
        <div
          className="absolute inset-y-0 w-[2px]"
          style={{ left: `${pctLimit}%`, background: 'rgb(var(--crit))' }}
          title={`${limitLabel} ${format(limit)}${unit}`}
          aria-hidden
        />
      </div>

      <div className="mt-1.5 flex items-center justify-between gap-3 text-caption text-slate">
        <span className="whitespace-nowrap">
          {limitLabel}{' '}
          <span className="font-mono font-medium text-navy tnum">
            {format(limit)}
            {unit}
          </span>
        </span>
        {showHeadroom && (
          <span
            className={`whitespace-nowrap font-mono tnum ${
              headroom < 0 ? 'text-critical font-medium' : ''
            }`}
          >
            {headroom < 0 ? 'Over by ' : 'Headroom '}
            {format(Math.abs(headroom))}
            {unit}
            <span className="text-slate-light">
              {' '}
              · {Math.abs(headroomPct).toFixed(0)}%
            </span>
          </span>
        )}
        {meta}
      </div>
    </div>
  );
}
