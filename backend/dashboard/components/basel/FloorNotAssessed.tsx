import StatusPill from '@/components/ui/StatusPill';

/**
 * The honest render of a limit row whose regulatory floor did not resolve.
 *
 * `LimitBar` cannot express "no floor": it needs a number to place the breach
 * zone, the amber zone and the headroom readout, so any number handed to it —
 * an assumed 8%, a written-down 10% — becomes a real-looking verdict on a real
 * measurement. This renders the measurement, states why no comparison was made,
 * and draws no zones at all. It is deliberately not green and deliberately not
 * red (see `assessAgainstFloor` in `lib/api/values.ts`, which produces the
 * `assessed: false` outcome this component is the display half of).
 */
export default function FloorNotAssessed({
  label,
  value,
  unit = '%',
  reason,
  format = (v: number) => v.toFixed(1),
}: {
  label: string;
  /** The measured ratio, or null when it too could not be computed. */
  value: number | null;
  unit?: string;
  /** Plain-language reason the comparison could not be made. */
  reason: string;
  format?: (v: number) => string;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-3 mb-1.5">
        <span className="text-caption font-medium text-navy truncate">
          {label}
        </span>
        <span className="font-mono text-caption font-semibold tnum whitespace-nowrap text-navy">
          {value === null ? 'Not computed' : `${format(value)}${unit}`}
        </span>
      </div>
      <div
        className="relative h-3 rounded-sm border border-dashed border-border"
        style={{ background: 'rgb(var(--surface-hover))' }}
        role="img"
        aria-label={`${label} not assessed — ${reason}`}
      />
      <div className="mt-1.5 flex items-center justify-between gap-3 text-caption text-slate">
        <span className="min-w-0">{reason}</span>
        <StatusPill tone="pending">Not assessed</StatusPill>
      </div>
    </div>
  );
}
