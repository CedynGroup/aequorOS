import type { ReactNode } from 'react';

/**
 * Compact ▲/▼ change indicator with risk-semantic coloring and tabular
 * numerals. `invert` flips which direction counts as good (e.g. cost or
 * exposure metrics where a decrease is favorable).
 */
export function DeltaBadge({
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

  const tone = isZero && neutralWhenZero ? 'text-slate' : good ? 'text-success' : 'text-critical';

  return (
    <span
      className={`inline-flex items-center gap-0.5 whitespace-nowrap font-mono text-caption font-medium tnum ${tone} ${className}`}
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

export type DeltaDirection = 'up' | 'down' | 'flat';
export type DeltaFavorability = 'favorable' | 'adverse' | 'neutral';

const DIRECTION_GLYPH: Record<DeltaDirection, string> = { up: '▲', down: '▼', flat: '–' };
const DIRECTION_WORD: Record<DeltaDirection, string> = {
  up: 'increased',
  down: 'decreased',
  flat: 'unchanged',
};
const FAVORABILITY_TONE: Record<DeltaFavorability, string> = {
  favorable: 'text-success',
  adverse: 'text-critical',
  neutral: 'text-slate',
};

/**
 * A change indicator whose SHAPE and COLOR are set independently: `direction`
 * chooses the triangle, `favorability` chooses the tone. Decouples the two
 * axes a single signed delta conflates (an NPL rise is a red ▲, a CAR rise a
 * green ▲). `children` is the already-formatted delta text.
 */
export function SemanticDelta({
  direction,
  favorability,
  children,
  className = '',
}: {
  direction: DeltaDirection;
  favorability: DeltaFavorability;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap font-mono tnum ${FAVORABILITY_TONE[favorability]} ${className}`}
    >
      <span aria-hidden className="text-[9px] leading-none">
        {DIRECTION_GLYPH[direction]}
      </span>
      <span className="sr-only">
        {DIRECTION_WORD[direction]}, {favorability}
      </span>
      {children}
    </span>
  );
}
