import type { ReactNode } from 'react';
import DeltaBadge from './DeltaBadge';

export type KpiStatus = 'ok' | 'warn' | 'crit';

const edgeStyles: Record<KpiStatus, string> = {
  ok: 'inset 3px 0 0 rgb(var(--ok))',
  warn: 'inset 3px 0 0 rgb(var(--warn))',
  crit: 'inset 3px 0 0 rgb(var(--crit))',
};

/**
 * A KPI value that is a PHRASE rather than a figure.
 *
 * The fail-closed states pass words where a number normally goes — "Not
 * computable", "Not assessed", "None". At the 28px mono KPI size a fourteen-
 * character phrase is wider than the tile and, because the value is
 * `whitespace-nowrap` with nothing clipping it, it spilled straight across the
 * card border into the neighbouring stat (seen on the SDI capital view's
 * "Capital headroom"). Truncating it instead would be worse: a half-shown
 * regulatory state reads as a rendering bug rather than a deliberate refusal.
 *
 * So a phrase is typeset one step down and allowed to wrap; anything carrying a
 * digit — every real figure, including "GHS 1.4B" and "15.83" — keeps the
 * tabular KPI treatment unchanged.
 */
function isPhraseValue(value: string | number): boolean {
  return typeof value === 'string' && !/\d/.test(value) && /[A-Za-z]{3,}/.test(value);
}

/**
 * Dense KPI stat for module dashboards: micro label, big tabular-numeral
 * value, optional unit, delta vs the prior period, a sparkline slot, and a
 * status edge-glow on the left border.
 */
export default function KpiStat({
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
  /** Pre-formatted display value (string) or a raw number. */
  value: string | number;
  unit?: string;
  delta?: number;
  deltaSuffix?: string;
  deltaDecimals?: number;
  /** When true, a negative delta is the good outcome. */
  invertDelta?: boolean;
  /** Colors the left edge glow: ok / warn / crit. */
  status?: KpiStatus;
  /** Slot for a <Sparkline /> or any small inline visual. */
  sparkline?: ReactNode;
  /** Secondary caption under the value (threshold, basis, etc.). */
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`card px-4 py-3.5 flex flex-col gap-1.5 min-w-0 ${className}`}
      style={status ? { boxShadow: edgeStyles[status] } : undefined}
    >
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        {label}
      </p>

      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-1 min-w-0">
          <span
            className={`font-mono ${
              isPhraseValue(value)
                ? 'text-h2 whitespace-normal'
                : 'text-kpi whitespace-nowrap'
            } text-navy tnum`}
            title={typeof value === 'number' ? String(value) : value}
          >
            {typeof value === 'number' ? String(value) : value}
          </span>
          {unit && <span className="text-body text-slate shrink-0">{unit}</span>}
        </div>
        {sparkline && <div className="shrink-0 pb-1">{sparkline}</div>}
      </div>

      {(delta !== undefined || hint) && (
        <div className="flex items-center justify-between gap-2 min-w-0">
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
          {hint && (
            <span className="text-caption text-slate">{hint}</span>
          )}
        </div>
      )}
    </div>
  );
}
