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
  bufferLabel = 'Internal buffer',
  status,
  decimals = 1,
  suffix = '%',
  thresholdLabel = 'Regulatory minimum',
  higherIsBetter = true,
}: {
  label: string;
  value: number;
  threshold: number;
  internalBuffer?: number;
  bufferLabel?: string;
  status: StatusTone;
  decimals?: number;
  suffix?: string;
  /** Caption for the threshold marker. Defaults to "Regulatory minimum". */
  thresholdLabel?: string;
  /**
   * Whether a higher value is better (a floor, e.g. LCR/CAR). Set false for
   * ceiling limits (e.g. ΔEVE/Tier1, NOP/Tier1) where staying below the
   * threshold is the compliant outcome — flips the variance colour.
   */
  higherIsBetter?: boolean;
}) {
  const max = Math.max(value, threshold) * 1.4;
  const pctValue = Math.min(100, (value / max) * 100);
  const pctThreshold = (threshold / max) * 100;
  const pctBuffer = internalBuffer ? (internalBuffer / max) * 100 : null;

  const fillColor =
    status === 'breach' || status === 'critical'
      ? 'rgb(var(--crit))'
      : status === 'approaching' || status === 'amber'
      ? 'rgb(var(--warn))'
      : 'rgb(var(--ok))';

  const variance = value - threshold;
  const varianceSign = variance >= 0 ? '+' : '';
  const varianceIsGood = higherIsBetter ? variance >= 0 : variance <= 0;

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
            varianceIsGood ? 'text-success' : 'text-critical'
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
          {thresholdLabel}{' '}
          <span className="font-mono font-medium text-navy tabular-nums">
            {threshold}
            {suffix}
          </span>
        </span>
        {internalBuffer && (
          <span>
            {bufferLabel}{' '}
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
