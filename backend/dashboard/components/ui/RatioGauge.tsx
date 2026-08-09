import type { StatusTone } from './StatusPill';

/**
 * Headline ratio gauge — the approved half-arc: value sweeps the arc, the
 * regulatory threshold is a tick, the arc color is the compliance state.
 * Used for LCR, NSFR, CAR-style module headline metrics.
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
  // Scale: the threshold sits at ~55% of the sweep so headroom reads as arc.
  const max = Math.max(Math.abs(value), Math.abs(threshold)) * 1.4 || 1;
  const fracValue = Math.min(1, Math.max(0, Math.abs(value) / max));
  const fracThreshold = Math.min(1, Math.max(0, Math.abs(threshold) / max));

  // Half-arc geometry: r=57 centred at (65,70) in a 130×76 box.
  const R = 57;
  const CX = 65;
  const CY = 70;
  const ARC_LEN = Math.PI * R;
  const tickAngle = Math.PI * (1 - fracThreshold);
  const tickX1 = CX + (R - 7) * Math.cos(tickAngle);
  const tickY1 = CY - (R - 7) * Math.sin(tickAngle);
  const tickX2 = CX + (R + 7) * Math.cos(tickAngle);
  const tickY2 = CY - (R + 7) * Math.sin(tickAngle);

  const arcColor =
    status === 'breach' || status === 'critical'
      ? 'rgb(var(--crit))'
      : status === 'approaching' || status === 'amber'
      ? 'rgb(var(--warn))'
      : 'rgb(var(--ok))';

  const variance = value - threshold;
  const varianceSign = variance >= 0 ? '+' : '';
  const varianceIsGood = higherIsBetter ? variance >= 0 : variance <= 0;

  return (
    <div className="card p-6 flex items-center gap-6">
      <svg
        width="130"
        height="76"
        viewBox="0 0 130 76"
        role="img"
        aria-label={`${label} ${value.toFixed(decimals)}${suffix}, ${thresholdLabel} ${threshold}${suffix}`}
        className="shrink-0"
      >
        <path
          d={`M${CX - R},${CY} A${R},${R} 0 0 1 ${CX + R},${CY}`}
          fill="none"
          stroke="rgb(var(--surface-hover))"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <path
          d={`M${CX - R},${CY} A${R},${R} 0 0 1 ${CX + R},${CY}`}
          fill="none"
          stroke={arcColor}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={`${fracValue * ARC_LEN} ${ARC_LEN}`}
        />
        <line
          x1={tickX1}
          y1={tickY1}
          x2={tickX2}
          y2={tickY2}
          stroke="rgb(var(--text-faint))"
          strokeWidth="2"
          aria-hidden
        />
        <text
          x={CX}
          y={CY - 10}
          textAnchor="middle"
          className="fill-navy font-mono font-semibold tabular-nums"
          style={{ fontSize: 21 }}
        >
          {value.toFixed(decimals)}
          {suffix}
        </text>
      </svg>

      <div className="min-w-0">
        <p className="text-caption font-medium text-slate uppercase tracking-wider">
          {label}
        </p>
        <p
          className={`mt-1 text-body font-mono font-medium tabular-nums ${
            varianceIsGood ? 'text-success' : 'text-critical'
          }`}
        >
          {varianceSign}
          {variance.toFixed(decimals)} pts vs {thresholdLabel.toLowerCase()}
        </p>
        <p className="mt-1.5 text-caption text-slate">
          {thresholdLabel}{' '}
          <span className="font-mono font-medium text-navy tabular-nums">
            {threshold}
            {suffix}
          </span>
          {internalBuffer !== undefined && (
            <>
              {' · '}
              {bufferLabel}{' '}
              <span className="font-mono font-medium text-navy tabular-nums">
                {internalBuffer}
                {suffix}
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
