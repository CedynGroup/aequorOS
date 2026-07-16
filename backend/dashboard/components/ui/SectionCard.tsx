import type { ReactNode } from 'react';
import { Clock } from 'lucide-react';
import { fmtTimestamp } from '@/lib/api/values';

/**
 * Standard module-page building block: a card with a title row + actions
 * slot, an optional un-padded body (tables/charts), and a footer meta row
 * for the last-computed timestamp and a RunBadge slot.
 */
export default function SectionCard({
  title,
  subtitle,
  actions,
  children,
  footer,
  computedAt,
  runBadge,
  noPadding = false,
  className = '',
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Free-form footer content; rendered alongside computedAt / runBadge. */
  footer?: ReactNode;
  /** Last-computed timestamp, shown in the footer meta row. */
  computedAt?: Date;
  /** Slot for a <RunBadge /> in the footer meta row. */
  runBadge?: ReactNode;
  /** Removes body padding — for tables and charts that bleed to the edges. */
  noPadding?: boolean;
  className?: string;
}) {
  const hasFooter = Boolean(footer || computedAt || runBadge);

  return (
    <section className={`card overflow-hidden ${className}`}>
      <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border-light">
        <div className="min-w-0">
          <h3 className="text-h3 text-navy">{title}</h3>
          {subtitle && (
            <p className="mt-0.5 text-caption text-slate">{subtitle}</p>
          )}
        </div>
        {actions && (
          <div className="shrink-0 flex items-center gap-2">{actions}</div>
        )}
      </div>

      <div className={noPadding ? '' : 'p-5'}>{children}</div>

      {hasFooter && (
        <div className="flex items-center justify-between gap-3 flex-wrap px-5 py-2.5 border-t border-border-light bg-surface/60">
          <div className="flex items-center gap-3 min-w-0 text-caption text-slate">
            {computedAt && (
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                <Clock size={11} aria-hidden />
                Computed{' '}
                <span className="font-mono tnum">{fmtTimestamp(computedAt)}</span>
              </span>
            )}
            {footer}
          </div>
          {runBadge && <div className="shrink-0">{runBadge}</div>}
        </div>
      )}
    </section>
  );
}
