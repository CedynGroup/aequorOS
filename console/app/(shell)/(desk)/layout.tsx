'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

/**
 * Markets Desk section frame: the sidebar carries ONE "Markets Desk" entry;
 * the desk's surfaces are this horizontal top nav — the same section-frame
 * pattern as the Developer group. Determinations first: the weekly
 * determination screen is the desk's working surface (spec §11a); the
 * methodology register (Track 2) sits deliberately apart from it.
 */

const TABS = [
  { href: '/desk/determinations', label: 'Research Desk' },
  { href: '/desk/observations', label: 'Observations' },
  { href: '/desk/curves', label: 'Curves' },
  { href: '/desk/methodology', label: 'Methodology' },
  { href: '/desk/sources', label: 'Sources' },
  { href: '/desk/entitlements', label: 'Entitlements' },
  { href: '/desk/publications', label: 'Publications' },
] as const;

export default function DeskLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div>
      <div className="mb-6 border-b border-border-light">
        <div className="flex items-end gap-1">
          {TABS.map(({ href, label }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={`-mb-px rounded-t-md border-b-2 px-4 py-2 text-body font-medium transition-colors ${
                  active
                    ? 'border-action text-action'
                    : 'border-transparent text-slate hover:bg-surface hover:text-ink'
                }`}
              >
                {label}
              </Link>
            );
          })}
        </div>
      </div>
      {children}
    </div>
  );
}
