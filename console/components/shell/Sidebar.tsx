'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import BrandLogo from '@/components/BrandLogo';
import { NAV_GROUPS, isNavActive } from './nav';

const COLLAPSE_STORAGE_KEY = 'aeq-console-sidebar-collapsed';

/**
 * The console's grouped, collapsible left rail. Collapsed state persists to
 * localStorage. Dark rail (`bg-nav`) to match the bank dashboard's sidebar
 * grammar; the console is dark-only so there is no theme variant.
 */
export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1');
    } catch {
      // storage unavailable — stay expanded
    }
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      try {
        window.localStorage.setItem(COLLAPSE_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // ignore
      }
      return next;
    });
  };

  return (
    <aside
      className={`${
        collapsed ? 'w-[68px]' : 'w-60'
      } sticky top-0 flex h-screen shrink-0 flex-col bg-nav text-white transition-[width] duration-200`}
    >
      <div
        className={`flex h-16 items-center border-b border-white/10 ${
          collapsed ? 'justify-center px-2' : 'px-5'
        }`}
      >
        {collapsed ? <BrandLogo inverse markOnly /> : <BrandLogo inverse subtitle="Operator" />}
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto overflow-x-hidden px-3 py-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            {collapsed ? (
              <div className="mx-2 mb-2 border-t border-white/10" aria-hidden />
            ) : (
              <p className="mb-2 px-3 text-micro font-medium uppercase tracking-wider text-white/40">
                {group.label}
              </p>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isNavActive(item.href, pathname);
                const Icon = item.icon;
                return (
                  <li key={item.href} className="group relative">
                    <Link
                      href={item.href}
                      aria-label={item.label}
                      aria-current={active ? 'page' : undefined}
                      className={`flex items-center gap-3 rounded text-body transition-colors ${
                        collapsed ? 'justify-center px-0 py-2.5' : 'px-3 py-2'
                      } ${
                        active
                          ? 'bg-white/10 text-white'
                          : 'text-white/75 hover:bg-white/5 hover:text-white'
                      }`}
                    >
                      <Icon size={16} className="shrink-0" aria-hidden />
                      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
                    </Link>
                    {collapsed && (
                      <span
                        role="tooltip"
                        className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded border border-white/15 bg-nav px-2.5 py-1.5 text-caption text-white opacity-0 shadow-pop transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                      >
                        {item.label}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/10 p-3">
        {!collapsed && (
          <p className="mb-2 px-2 text-micro leading-relaxed text-white/40">
            Staff tooling — bank tenants never see this surface.
          </p>
        )}
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={`inline-flex w-full items-center gap-2 rounded px-2 py-2 text-caption text-white/60 transition-colors hover:bg-white/5 hover:text-white ${
            collapsed ? 'justify-center' : ''
          }`}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} aria-hidden />
          ) : (
            <>
              <PanelLeftClose size={16} aria-hidden />
              <span>Collapse</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
