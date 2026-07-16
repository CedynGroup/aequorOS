/**
 * Compact ▲/▼ change indicator with risk-semantic coloring and tabular
 * numerals. `invert` flips which direction counts as good (e.g. cost or
 * exposure metrics where a decrease is favorable).
 */
export default function DeltaBadge({
  value,
  suffix = '',
  decimals = 1,
  invert = false,
  neutralWhenZero = true,
  className = '',
}: {
  value: number;
  /** Unit appended to the number, e.g. ' pts', '%', ' bps'. */
  suffix?: string;
  decimals?: number;
  /** When true, a negative delta is the good outcome. */
  invert?: boolean;
  /** Renders a muted em-dash style badge when the delta is exactly zero. */
  neutralWhenZero?: boolean;
  className?: string;
}) {
  const isZero = value === 0;
  const positive = value > 0;
  const good = invert ? !positive : positive;

  const tone =
    isZero && neutralWhenZero
      ? 'text-slate'
      : good
      ? 'text-success'
      : 'text-critical';

  return (
    <span
      className={`inline-flex items-center gap-0.5 text-caption font-medium font-mono tnum whitespace-nowrap ${tone} ${className}`}
    >
      <span aria-hidden className="text-[9px] leading-none">
        {isZero ? '' : positive ? '▲' : '▼'}
      </span>
      {positive ? '+' : ''}
      {value.toFixed(decimals)}
      {suffix}
    </span>
  );
}
