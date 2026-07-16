import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

export default function PageHeader({
  breadcrumbs,
  title,
  subtitle,
  action,
  asOf,
}: {
  breadcrumbs?: { label: string; href?: string }[];
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  asOf?: string;
}) {
  return (
    <div className="border-b border-border-light bg-surface-raised">
      <div className="px-8 py-5">
        {breadcrumbs && breadcrumbs.length > 0 && (
          <nav
            aria-label="Breadcrumb"
            className="flex items-center gap-1 text-caption text-slate mb-2"
          >
            {breadcrumbs.map((b, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && (
                  <ChevronRight size={12} className="text-slate-light" aria-hidden />
                )}
                {b.href ? (
                  <a href={b.href} className="hover:text-action transition-colors">
                    {b.label}
                  </a>
                ) : (
                  <span>{b.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}

        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <h1 className="text-display text-navy">{title}</h1>
            {subtitle && (
              <p className="mt-1 text-body text-slate">{subtitle}</p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {asOf && (
              <span className="text-caption text-slate">
                As of <span className="font-mono font-medium text-navy">{asOf}</span>
              </span>
            )}
            {action}
          </div>
        </div>
      </div>
    </div>
  );
}
