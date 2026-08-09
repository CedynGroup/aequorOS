import { TriangleAlert } from 'lucide-react';

/**
 * Shared chip primitives for the Markets tab: the mono code chip used for
 * source systems, AEQ.* curve tickers, and index codes, plus the curve-type
 * badge and the truthful-naming marker for the synthetic discounting proxy
 * (spec §8: never present a modelled curve as a traded one).
 */

/** Small monospace code chip — source systems, curve tickers, index codes. */
export function MonoChip({
  children,
  className = '',
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center px-1.5 py-0.5 rounded border border-border-light bg-surface text-[10px] font-mono uppercase tracking-wider text-slate whitespace-nowrap ${className}`}
    >
      {children}
    </span>
  );
}

const CURVE_TYPE_LABELS: Record<string, string> = {
  zero: 'Zero',
  forward: 'Forward',
  discount: 'Discounting',
  sovereign: 'Sovereign',
  interbank: 'Interbank',
  swap: 'Swap',
  credit_spread: 'Credit spread',
};

/** Curve-family badge: zero / forward / discounting / sovereign / ... */
export function CurveTypeBadge({
  curveType,
  className = '',
}: {
  curveType: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded border border-border bg-surface text-[10px] font-medium uppercase tracking-wider text-navy whitespace-nowrap ${className}`}
    >
      {CURVE_TYPE_LABELS[curveType] ?? curveType.replace(/_/g, ' ')}
    </span>
  );
}

/**
 * Truthful-naming marker for the synthetic discounting proxy: it is a
 * modelled curve with a disclosed methodology, not a traded OIS.
 */
export function SyntheticProxyBadge({ className = '' }: { className?: string }) {
  return (
    <span
      title="Modelled discounting proxy — not a traded curve. Methodology is disclosed and versioned."
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-warning/30 bg-warning-light text-warning text-micro font-medium uppercase tracking-wider whitespace-nowrap ${className}`}
    >
      <TriangleAlert size={10} aria-hidden />
      Synthetic proxy
    </span>
  );
}
