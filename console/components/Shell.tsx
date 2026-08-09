'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Activity, Building2, LogOut, PlusCircle } from 'lucide-react';
import { apiHost, clearToken } from '@/lib/api';
import type { ReactNode } from 'react';

const NAV = [
  { href: '/tenants', label: 'Tenants', icon: Building2 },
  { href: '/onboard', label: 'Onboard', icon: PlusCircle },
  { href: '/operations', label: 'Operations', icon: Activity },
] as const;

/**
 * Thin console shell: fixed left rail (brand + nav) and a top header carrying
 * the environment badge (which API this console is pointed at — the single
 * most important piece of operator context) and the session controls.
 */
export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <div className="flex min-h-screen">
      {/* Left rail */}
      <aside className="fixed inset-y-0 left-0 z-20 flex w-56 flex-col bg-nav">
        <div className="px-5 pb-4 pt-5">
          <div className="text-h3 font-semibold text-white">AequorOS</div>
          <div className="text-micro uppercase tracking-widest text-slate">Operator</div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-body font-medium transition-colors ${
                  active
                    ? 'bg-action-light text-action'
                    : 'text-[color:var(--nav-text)] hover:bg-surface hover:text-ink'
                }`}
              >
                <Icon size={16} />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="px-5 py-4 text-micro text-slate-light">
          Staff tooling — bank tenants never see this surface.
        </div>
      </aside>

      {/* Main column */}
      <div className="ml-56 flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-border-light bg-surface-alt/95 px-6 backdrop-blur">
          <div className="flex items-center gap-2">
            <span
              className="rounded border border-border-light bg-surface px-2 py-0.5 font-mono text-micro text-slate"
              title={`Operator API base: ${apiHost()}`}
            >
              API · {apiHost()}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {/* The operator API does not yet return an operator identity
                (email/claims). Until it does, show the honest truth: this is
                a dev token session. */}
            <span className="rounded bg-warning-light px-2 py-0.5 text-micro font-medium uppercase tracking-wide text-warning">
              dev session
            </span>
            <button
              type="button"
              onClick={() => {
                clearToken();
                router.replace('/login');
              }}
              className="inline-flex items-center gap-1.5 rounded border border-border-light px-2.5 py-1 text-caption font-medium text-slate hover:bg-surface hover:text-ink"
            >
              <LogOut size={13} />
              Sign out
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
