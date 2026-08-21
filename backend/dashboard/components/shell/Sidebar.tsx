'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Gauge,
  BellRing,
  CandlestickChart,
  Layers,
  Activity,
  Database,
  Droplet,
  DollarSign,
  ShieldCheck,
  GitBranch,
  TrendingUp,
  BrainCircuit,
  FileBarChart2,
  FileCheck2,
  Landmark,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import Logo from './Logo';
import { centralBankName } from '@/lib/format';
import { isHrefVisible } from '@/lib/modules';
import { useModuleScope } from './BankContext';

const COLLAPSE_STORAGE_KEY = 'aeq-sidebar-collapsed';

type NavItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  /** SDI-tenant label override (an SDI is not a Basel institution). */
  sdiLabel?: string;
};

const SDI_CLASS = 'sdi';

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: 'Command',
    items: [
      { href: '/', label: 'Command Center', icon: LayoutDashboard },
      { href: '/risk', label: 'Risk & Limits', icon: Gauge },
      { href: '/alerts', label: 'Alerts', icon: BellRing },
    ],
  },
  {
    label: 'Markets',
    items: [
      { href: '/markets', label: 'Markets', icon: CandlestickChart },
      { href: '/positions', label: 'Positions', icon: Layers },
    ],
  },
  {
    label: 'Modules',
    items: [
      { href: '/irr', label: 'IRRBB', icon: Activity },
      { href: '/liquidity', label: 'Liquidity', icon: Droplet },
      { href: '/fx', label: 'FX', icon: DollarSign },
      { href: '/basel', label: 'Basel Capital', sdiLabel: 'Regulatory Capital', icon: ShieldCheck },
      { href: '/ftp', label: 'FTP', icon: GitBranch },
      { href: '/forecasting', label: 'Forecasting', icon: TrendingUp },
      { href: '/behavioral', label: 'Behavioral', icon: BrainCircuit },
    ],
  },
  {
    label: 'Data',
    items: [
      { href: '/data-engine', label: 'Data Engine', icon: Database },
    ],
  },
  {
    label: 'Governance',
    items: [
      { href: '/reports', label: 'Reports', icon: FileBarChart2 },
      { href: '/institution', label: 'Institution Profile', icon: Landmark },
      { href: '/submissions', label: 'Regulatory Reporting', icon: FileCheck2 },
      { href: '/settings', label: 'Settings', icon: Settings },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  const [collapsed, setCollapsed] = useState(false);

  // Scope the nav to the active institution type (docs/sdi.md §3.1/§6.3): drop
  // items whose module the tenant's class is not entitled to, then drop any
  // group left empty. Unscoped tenants (banks) keep every item.
  const visibleGroups = groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => isHrefVisible(item.href, moduleScope)),
    }))
    .filter((group) => group.items.length > 0);

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

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <aside
      className={`${
        collapsed ? 'w-[68px]' : 'w-64'
      } shrink-0 bg-nav text-white flex flex-col h-screen lg:sticky top-0 transition-[width] duration-200`}
    >
      <div
        className={`h-16 flex items-center border-b border-white/10 ${
          collapsed ? 'justify-center px-2' : 'px-5'
        }`}
      >
        <Logo variant="dark" showWordmark={!collapsed} />
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-3 space-y-5">
        {visibleGroups.map((group) => (
          <div key={group.label}>
            {collapsed ? (
              <div className="mx-2 mb-2 border-t border-white/10" aria-hidden />
            ) : (
              <p className="px-3 text-micro font-medium uppercase tracking-wider text-white/40 mb-2">
                {group.label}
              </p>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(item.href);
                const Icon = item.icon;
                const label =
                  item.sdiLabel && moduleScope.institutionClass === SDI_CLASS
                    ? item.sdiLabel
                    : item.label;
                return (
                  <li key={item.href} className="relative group">
                    <Link
                      href={item.href}
                      aria-label={label}
                      className={`flex items-center gap-3 rounded text-body transition-colors ${
                        collapsed ? 'justify-center px-0 py-2.5' : 'px-3 py-2'
                      } ${
                        active
                          ? 'bg-white/10 text-white'
                          : 'text-white/75 hover:bg-white/5 hover:text-white'
                      }`}
                    >
                      <Icon size={16} className="shrink-0" aria-hidden />
                      {!collapsed && <span className="flex-1 truncate">{label}</span>}
                    </Link>
                    {collapsed && (
                      <span
                        role="tooltip"
                        className="pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50 whitespace-nowrap rounded bg-nav border border-white/15 px-2.5 py-1.5 text-caption text-white opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity shadow-pop"
                      >
                        {label}
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
          <div className="rounded bg-white/5 p-3 mb-2">
            <p className="text-caption text-white/50 uppercase tracking-wider font-medium">
              Environment
            </p>
            <p className="mt-1 text-body text-white">Demo · Sandbox data</p>
            <p className="mt-1 text-caption text-white/50">
              {`Synthetic ${centralBankName()} licensee`}
            </p>
          </div>
        )}
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={`w-full inline-flex items-center gap-2 rounded px-2 py-2 text-caption text-white/60 hover:text-white hover:bg-white/5 transition-colors ${
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
