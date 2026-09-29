"use client";

import type { ReactNode } from "react";
import { SkeletonLine } from "./Skeleton";

/**
 * Standard card wrapper for a chart: title row + actions slot, fixed-height
 * body, and a built-in skeleton loading state.
 *
 * Pass the SAME `height` to the chart inside it — `components/bi/EChart` sizes a
 * canvas in pixels rather than to its container, so the two numbers agreeing is
 * what keeps the card from clipping or leaving a gap.
 *
 * `trust` is the actions slot's FIRST occupant, reserved for the reconciliation
 * verdict (`components/bi/TrustBadge.tsx::ReconciliationTrustBadge`) —
 * `docs/bi.md` §Insights layer: "`TrustBadge` goes in the `ChartFrame` actions
 * slot". It is its own prop rather than something callers splice into
 * `actions` so a page that already fills the slot cannot drop the badge by
 * accident. It belongs on a chart whose figures ARE the institution's computed
 * position at the page's reporting date — the thing the checks compare; a
 * projection path or a market curve is not one, and carries none. Absent, the
 * header renders exactly as before.
 */
export default function ChartFrame({
  title,
  subtitle,
  trust,
  actions,
  height = 280,
  loading = false,
  footer,
  children,
  className = "",
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  /** The reconciliation verdict for the figures drawn. See above. */
  trust?: ReactNode;
  actions?: ReactNode;
  /** Pixel height of the chart body (the ResponsiveContainer viewport). */
  height?: number;
  /** Renders a skeleton shimmer instead of the chart body. */
  loading?: boolean;
  /** Meta row under the chart (source, basis, run info). */
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card overflow-hidden ${className}`}>
      <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h3 className="text-h3 text-navy">{title}</h3>
          {subtitle && (
            <p className="mt-0.5 text-caption text-slate">{subtitle}</p>
          )}
        </div>
        {(trust || actions) && (
          <div className="shrink-0 flex items-center gap-2">
            {trust}
            {actions}
          </div>
        )}
      </div>

      <div className="px-3 pb-4" aria-busy={loading || undefined}>
        {loading ? (
          <div
            className="flex flex-col justify-end gap-3 px-2"
            style={{ height }}
            aria-label="Loading chart"
          >
            <div className="flex-1 bg-surface rounded animate-pulse" />
            <div className="flex items-center gap-4 px-2">
              <SkeletonLine width="18%" height={8} />
              <SkeletonLine width="14%" height={8} />
              <SkeletonLine width="16%" height={8} />
            </div>
          </div>
        ) : (
          <div style={{ height }}>{children}</div>
        )}
      </div>

      {footer && (
        <div className="px-5 py-2.5 border-t border-border-light text-caption text-slate flex items-center gap-3 flex-wrap">
          {footer}
        </div>
      )}
    </section>
  );
}
