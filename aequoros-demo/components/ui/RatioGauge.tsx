import type { StatusTone } from './StatusPill';

/**
 * Large ratio display widget — current value, threshold, internal buffer.
 * Used for LCR, NSFR, CAR-style headline metrics.
 */
export default function RatioGauge({
  label,
  value,
  threshold,
  internalBuffer,
  status,
  decimals = 1,
  suffix = '%',
}: {
  label: string;
  value: number;
  threshold: number;
  internalBuffer?: number;
  status: StatusTone;
  decimals?: number;
  suffix?: string;
}) {
  const max = Math.max(value, threshold) * 1.4;
  const pctValue = Math.min(100, (value / max) * 100);
  const pctThreshold = (threshold / max) * 100;
  const pctBuffer = internalBuffer ? (internalBuffer / max) * 100 : null;

  const fillColor =
    status === 'breach' || status === 'critical'
      ? '#B3261E'
      : status === 'approaching' || status === 'amber'
      ? '#C97C00'
      : '#0E8A4F';

  const variance = value - threshold;
  const varianceSign = variance >= 0 ? '+' : '';

  return (
    <div className="card p-6">
      <p className="text-caption font-medium text-slate uppercase tracking-wider">
        {label}
      </p>

      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-mono text-[44px] leading-none font-semibold text-navy tabular-nums">
          {value.toFixed(decimals)}
        </span>
        <span className="text-h2 text-navy">{suffix}</span>
        <span
          className={`ml-2 text-body font-mono font-medium tabular-nums ${
            variance >= 0 ? 'text-success' : 'text-critical'
          }`}
        >
          {varianceSign}
          {variance.toFixed(decimals)} pts
        </span>
      </div>

      <div className="mt-5 relative h-2.5 bg-surface rounded-full overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${pctValue}%`, background: fillColor }}
        />
        {/* Threshold marker */}
        <div
          className="absolute inset-y-0 w-px bg-navy"
          style={{ left: `${pctThreshold}%` }}
          aria-hidden
        />
        {pctBuffer && (
          <div
            className="absolute inset-y-0 w-px bg-slate-light/60"
            style={{ left: `${pctBuffer}%` }}
            aria-hidden
          />
        )}
      </div>

      <div className="mt-2 flex items-center justify-between text-caption text-slate">
        <span>
          Regulatory minimum{' '}
          <span className="font-mono font-medium text-navy tabular-nums">
            {threshold}
            {suffix}
          </span>
        </span>
        {internalBuffer && (
          <span>
            Internal buffer{' '}
            <span className="font-mono font-medium text-navy tabular-nums">
              {internalBuffer}
              {suffix}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
