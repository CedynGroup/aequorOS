'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Activity,
  Droplet,
  DollarSign,
  ShieldCheck,
  GitBranch,
  TrendingUp,
  FileBarChart2,
  FileCheck2,
  Settings,
} from 'lucide-react';
import Logo from './Logo';

type NavItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  code?: string;
  postMvp?: boolean;
};

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: 'Overview',
    items: [{ href: '/', label: 'Home', icon: LayoutDashboard }],
  },
  {
    label: 'Modules',
    items: [
      {
        href: '/irr',
        label: 'Interest Rate Risk',
        icon: Activity,
        code: '01',
        postMvp: true,
      },
      { href: '/liquidity', label: 'Liquidity Risk', icon: Droplet, code: '02' },
      {
        href: '/fx',
        label: 'FX Risk',
        icon: DollarSign,
        code: '03',
        postMvp: true,
      },
      { href: '/basel', label: 'Basel Capital', icon: ShieldCheck, code: '04' },
      {
        href: '/ftp',
        label: 'Funds Transfer Pricing',
        icon: GitBranch,
        code: '05',
        postMvp: true,
      },
      {
        href: '/forecasting',
        label: 'Balance Sheet Forecasting',
        icon: TrendingUp,
        code: '06',
      },
    ],
  },
  {
    label: 'Filings & admin',
    items: [
      { href: '/reports', label: 'Reports Library', icon: FileBarChart2 },
      { href: '/submissions', label: 'Regulatory Submissions', icon: FileCheck2 },
      { href: '/settings', label: 'Settings', icon: Settings },
    ],
  },
];

export default function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <aside className="w-64 shrink-0 bg-navy text-white flex flex-col h-screen lg:sticky top-0">
      <div className="px-5 h-16 flex items-center border-b border-white/10">
        <Logo variant="dark" />
      </div>

      <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-6">
        {groups.map((group) => (
          <div key={group.label}>
            <p className="px-3 text-micro font-medium uppercase tracking-wider text-white/40 mb-2">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={`flex items-center gap-3 px-3 py-2 rounded text-body transition-colors ${
                        active
                          ? 'bg-white/10 text-white'
                          : 'text-white/75 hover:bg-white/5 hover:text-white'
                      }`}
                    >
                      <Icon size={16} className="shrink-0" aria-hidden />
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.postMvp && (
                        <span className="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider bg-white/10 text-white/60 border border-white/15">
                          Post-MVP
                        </span>
                      )}
                      {item.code && (
                        <span
                          className={`font-mono text-[10px] ${
                            active ? 'text-action' : 'text-white/30'
                          }`}
                        >
                          {item.code}
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/10 p-4">
        <div className="rounded bg-white/5 p-3">
          <p className="text-caption text-white/50 uppercase tracking-wider font-medium">
            Environment
          </p>
          <p className="mt-1 text-body text-white">Demo · Sandbox data</p>
          <p className="mt-1 text-caption text-white/50">
            Synthetic Bank of Ghana licensee
          </p>
        </div>
      </div>
    </aside>
  );
}
