import type { ReactNode } from 'react';
import { Clock } from 'lucide-react';
import { fmtTimestamp } from '@/lib/format';

/**
 * THE standard console module block: a card with a title row + actions slot,
 * an optional un-padded body (tables/charts bleed to the edges), and a footer
 * meta row for a last-computed timestamp and a RunBadge slot.
 *
 * Ported from the dashboard's SectionCard; `computedAt` accepts a Date or an
 * ISO string (the desk wire hands out strings).
 */
export function SectionCard({
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
  computedAt?: Date | string;
  /** Slot for a <RunBadge /> in the footer meta row. */
  runBadge?: ReactNode;
  /** Removes body padding — for tables and charts that bleed to the edges. */
  noPadding?: boolean;
  className?: string;
}) {
  const hasFooter = Boolean(footer || computedAt || runBadge);

  return (
    <section className={`card overflow-hidden ${className}`}>
      <div className="flex items-start justify-between gap-4 border-b border-border-light px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-h3 text-navy">{title}</h3>
          {subtitle && <p className="mt-0.5 text-caption text-slate">{subtitle}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>

      <div className={noPadding ? '' : 'p-5'}>{children}</div>

      {hasFooter && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-light bg-surface/60 px-5 py-2.5">
          <div className="flex min-w-0 items-center gap-3 text-caption text-slate">
            {computedAt && (
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                <Clock size={11} aria-hidden />
                Computed <span className="font-mono tnum">{fmtTimestamp(computedAt)}</span>
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
