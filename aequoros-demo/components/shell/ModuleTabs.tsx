'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export type Tab = { href: string; label: string };

export default function ModuleTabs({ tabs }: { tabs: Tab[] }) {
  const pathname = usePathname();
  return (
    <div className="bg-white border-b border-border-light px-8">
      <nav className="-mb-px flex gap-1 overflow-x-auto" aria-label="Module sections">
        {tabs.map((t) => {
          const active =
            t.href === pathname ||
            (t.href !== '/' && pathname.startsWith(t.href + '/')) ||
            (t.href.split('/').length > 2 && pathname === t.href);
          // Refined active match: exact match or first path-segment-after-href is empty
          const isActive = pathname === t.href;
          return (
            <Link
              key={t.href}
              href={t.href}
              className={`px-4 py-3 text-body font-medium border-b-2 whitespace-nowrap transition-colors ${
                isActive
                  ? 'border-action text-navy'
                  : 'border-transparent text-slate hover:text-navy hover:border-border'
              }`}
            >
              {t.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
