import type { ReactNode } from 'react';
import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

/**
 * Page header. UPGRADED from the original thin `{ title, sub }` header:
 * adds an optional breadcrumb trail, a right-aligned actions slot, and an
 * `asOf` stamp — while staying an in-flow block (the console's <main> owns the
 * page padding, so this is deliberately NOT a full-bleed bordered bar).
 *
 * Back-compat: `sub` is honored as an alias of `subtitle`, so every existing
 * `<PageHeader title=… sub=… />` call still renders unchanged.
 */
export function PageHeader({
  title,
  sub,
  subtitle,
  breadcrumbs,
  action,
  asOf,
  className = '',
}: {
  title: ReactNode;
  /** Legacy alias for `subtitle`. */
  sub?: ReactNode;
  subtitle?: ReactNode;
  breadcrumbs?: { label: string; href?: string }[];
  action?: ReactNode;
  asOf?: string;
  className?: string;
}) {
  const secondary = subtitle ?? sub;
  return (
    <div className={`mb-6 ${className}`}>
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav
          aria-label="Breadcrumb"
          className="mb-2 flex items-center gap-1 text-caption text-slate"
        >
          {breadcrumbs.map((b, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <ChevronRight size={12} className="text-slate-light" aria-hidden />}
              {b.href ? (
                <Link href={b.href} className="transition-colors hover:text-action">
                  {b.label}
                </Link>
              ) : (
                <span className="text-slate-light">{b.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-h1 text-navy">{title}</h1>
          {secondary && <p className="mt-1 text-body text-slate">{secondary}</p>}
        </div>
        {(action || asOf) && (
          <div className="flex items-center gap-3">
            {asOf && (
              <span className="text-caption text-slate">
                As of <span className="font-mono font-medium text-navy tnum">{asOf}</span>
              </span>
            )}
            {action}
          </div>
        )}
      </div>
    </div>
  );
}

/** Label / value row for definition-list style panels (kept from ui.tsx). */
export function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-caption uppercase tracking-wide text-slate">{label}</span>
      <span className="min-w-0 text-right text-body text-ink">{children}</span>
    </div>
  );
}
