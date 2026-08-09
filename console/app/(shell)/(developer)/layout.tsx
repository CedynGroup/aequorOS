'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

/**
 * Developer section frame: the sidebar carries ONE "Developer" entry; the
 * section's surfaces (Tenants, Onboard, Operations) are this horizontal top
 * nav — the same section-frame pattern the bank dashboard uses for module
 * subnavs. The route group keeps the URLs unchanged (/tenants, /onboard,
 * /operations).
 */

const TABS = [
  { href: '/tenants', label: 'Tenants' },
  { href: '/onboard', label: 'Onboard' },
  { href: '/operations', label: 'Operations' },
] as const;

export default function DeveloperLayout({ children }: { children: ReactNode }) {
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
