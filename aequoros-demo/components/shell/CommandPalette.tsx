'use client';

import { useEffect, useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search,
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
  ArrowRight,
} from 'lucide-react';

type Item = {
  id: string;
  label: string;
  hint?: string;
  href: string;
  icon: typeof LayoutDashboard;
  group: string;
};

const items: Item[] = [
  { id: 'home', label: 'Home / Overview Dashboard', href: '/', icon: LayoutDashboard, group: 'Pages' },

  { id: 'irr', label: 'Interest Rate Risk Dashboard', hint: 'Module 01', href: '/irr', icon: Activity, group: 'Modules' },
  { id: 'irr-scenarios', label: 'IRR — Rate Scenarios', href: '/irr/scenarios', icon: Activity, group: 'Modules' },
  { id: 'irr-positions', label: 'IRR — Position Viewer (IRS book)', href: '/irr/positions', icon: Activity, group: 'Modules' },
  { id: 'irr-hedging', label: 'IRR — AI Hedging Recommendations', href: '/irr/hedging', icon: Activity, group: 'Modules' },

  { id: 'lcr', label: 'Liquidity — LCR Dashboard', hint: 'Module 02', href: '/liquidity', icon: Droplet, group: 'Modules' },
  { id: 'nsfr', label: 'Liquidity — NSFR Dashboard', href: '/liquidity/nsfr', icon: Droplet, group: 'Modules' },
  { id: 'forecast', label: 'Liquidity — Cash Flow Forecast (LSTM)', href: '/liquidity/forecast', icon: Droplet, group: 'Modules' },
  { id: 'stress', label: 'Liquidity — Stress Scenarios', href: '/liquidity/stress', icon: Droplet, group: 'Modules' },
  { id: 'submission', label: 'Liquidity — BoG Submission Preview', href: '/liquidity/submission', icon: Droplet, group: 'Modules' },

  { id: 'fx', label: 'FX — Exposure Dashboard', hint: 'Module 03', href: '/fx', icon: DollarSign, group: 'Modules' },
  { id: 'fx-scenarios', label: 'FX — Currency Scenarios', href: '/fx/scenarios', icon: DollarSign, group: 'Modules' },
  { id: 'fx-prediction', label: 'FX — Rate Prediction', href: '/fx/prediction', icon: DollarSign, group: 'Modules' },
  { id: 'fx-hedging', label: 'FX — Hedging Dashboard', href: '/fx/hedging', icon: DollarSign, group: 'Modules' },

  { id: 'basel', label: 'Basel Capital — Dashboard', hint: 'Module 04', href: '/basel', icon: ShieldCheck, group: 'Modules' },
  { id: 'rwa', label: 'Basel — RWA Breakdown', href: '/basel/rwa', icon: ShieldCheck, group: 'Modules' },
  { id: 'capital-structure', label: 'Basel — Capital Structure', href: '/basel/structure', icon: ShieldCheck, group: 'Modules' },
  { id: 'basel-stress', label: 'Basel — Stress Testing', href: '/basel/stress', icon: ShieldCheck, group: 'Modules' },
  { id: 'basel-subs', label: 'Basel — Submissions', href: '/basel/submissions', icon: ShieldCheck, group: 'Modules' },

  { id: 'ftp', label: 'FTP — Yield Curve', hint: 'Module 05', href: '/ftp', icon: GitBranch, group: 'Modules' },
  { id: 'ftp-branches', label: 'FTP — Branch P&L', href: '/ftp/branches', icon: GitBranch, group: 'Modules' },
  { id: 'ftp-products', label: 'FTP — Product P&L', href: '/ftp/products', icon: GitBranch, group: 'Modules' },
  { id: 'ftp-rates', label: 'FTP — Rates Table', href: '/ftp/rates', icon: GitBranch, group: 'Modules' },

  { id: 'forecasting', label: 'Balance Sheet — Forecast Dashboard', hint: 'Module 06', href: '/forecasting', icon: TrendingUp, group: 'Modules' },
  { id: 'forecast-scenario', label: 'Balance Sheet — Scenario Builder', href: '/forecasting/scenario', icon: TrendingUp, group: 'Modules' },
  { id: 'forecast-rl', label: 'Balance Sheet — RL Optimizer', href: '/forecasting/optimizer', icon: TrendingUp, group: 'Modules' },
  { id: 'forecast-whatif', label: 'Balance Sheet — What-if Analysis', href: '/forecasting/whatif', icon: TrendingUp, group: 'Modules' },

  { id: 'reports', label: 'Reports Library', href: '/reports', icon: FileBarChart2, group: 'Filings' },
  { id: 'submissions', label: 'Regulatory Submissions', href: '/submissions', icon: FileCheck2, group: 'Filings' },
  { id: 'settings', label: 'Settings', href: '/settings', icon: Settings, group: 'Filings' },
];

export default function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const router = useRouter();

  const filtered = useMemo(() => {
    if (!query.trim()) return items;
    const q = query.toLowerCase();
    return items.filter(
      (it) =>
        it.label.toLowerCase().includes(q) ||
        it.hint?.toLowerCase().includes(q) ||
        it.group.toLowerCase().includes(q)
    );
  }, [query]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive((a) => Math.min(filtered.length - 1, a + 1));
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive((a) => Math.max(0, a - 1));
      }
      if (e.key === 'Enter' && filtered[active]) {
        e.preventDefault();
        router.push(filtered[active].href);
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, filtered, active, onClose, router]);

  if (!open) return null;

  // Group items
  const groups: Record<string, Item[]> = {};
  filtered.forEach((it) => {
    groups[it.group] = groups[it.group] ?? [];
    groups[it.group].push(it);
  });

  let runningIdx = 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-start justify-center pt-24 px-4"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-navy/30 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-xl bg-white border border-border rounded-lg shadow-pop overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border-light">
          <Search size={16} className="text-slate" aria-hidden />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search modules, screens, reports…"
            className="flex-1 outline-none text-body text-navy placeholder:text-slate"
          />
          <kbd className="text-[10px] font-mono text-slate bg-surface border border-border-light rounded px-1.5 py-0.5">
            ESC
          </kbd>
        </div>

        <div className="max-h-[420px] overflow-y-auto py-2">
          {filtered.length === 0 ? (
            <div className="px-5 py-8 text-center text-body text-slate">
              No results for &ldquo;{query}&rdquo;
            </div>
          ) : (
            Object.entries(groups).map(([group, gItems]) => (
              <div key={group} className="mb-2">
                <p className="px-4 py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                  {group}
                </p>
                <ul>
                  {gItems.map((it) => {
                    const idx = runningIdx++;
                    const isActive = idx === active;
                    const Icon = it.icon;
                    return (
                      <li key={it.id}>
                        <button
                          type="button"
                          onMouseEnter={() => setActive(idx)}
                          onClick={() => {
                            router.push(it.href);
                            onClose();
                          }}
                          className={`w-full flex items-center gap-3 px-4 py-2 text-body text-left ${
                            isActive ? 'bg-action-light text-navy' : 'text-navy/85 hover:bg-surface'
                          }`}
                        >
                          <Icon
                            size={14}
                            className={isActive ? 'text-action' : 'text-slate'}
                            aria-hidden
                          />
                          <span className="flex-1 truncate">{it.label}</span>
                          {it.hint && (
                            <span className="text-caption text-slate font-mono shrink-0">
                              {it.hint}
                            </span>
                          )}
                          <ArrowRight
                            size={12}
                            className={isActive ? 'text-action' : 'text-slate/0'}
                            aria-hidden
                          />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>

        <div className="border-t border-border-light px-4 py-2 flex items-center gap-4 text-caption text-slate">
          <span className="inline-flex items-center gap-1">
            <kbd className="text-[10px] font-mono bg-surface border border-border-light rounded px-1.5 py-0.5">↑↓</kbd>
            navigate
          </span>
          <span className="inline-flex items-center gap-1">
            <kbd className="text-[10px] font-mono bg-surface border border-border-light rounded px-1.5 py-0.5">↵</kbd>
            select
          </span>
          <span className="ml-auto">{filtered.length} results</span>
        </div>
      </div>
    </div>
  );
}
