'use client';

import { useEffect, useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Search,
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
  /** Extra search terms not present in the label. */
  keywords?: string;
};

const items: Item[] = [
  { id: 'home', label: 'Command Center', href: '/', icon: LayoutDashboard, group: 'Command', keywords: 'home overview dashboard cockpit' },
  { id: 'risk', label: 'Risk & Limits', href: '/risk', icon: Gauge, group: 'Command', keywords: 'limit utilization headroom breach appetite' },
  { id: 'alerts', label: 'Alerts', href: '/alerts', icon: BellRing, group: 'Command', keywords: 'breach notification warning' },

  { id: 'markets', label: 'Markets', href: '/markets', icon: CandlestickChart, group: 'Markets', keywords: 'rates curves fx spot yields treasury bills bonds' },
  { id: 'positions', label: 'Positions', href: '/positions', icon: Layers, group: 'Markets', keywords: 'portfolio balance sheet instruments book' },

  { id: 'irr', label: 'IRRBB — Interest Rate Risk Dashboard', hint: 'Module 01', href: '/irr', icon: Activity, group: 'Modules', keywords: 'eve nii repricing gap duration basis' },

  { id: 'lcr', label: 'Liquidity — LCR Dashboard', hint: 'Module 02', href: '/liquidity', icon: Droplet, group: 'Modules', keywords: 'coverage ratio hqla outflows' },
  { id: 'nsfr', label: 'Liquidity — NSFR Dashboard', href: '/liquidity/nsfr', icon: Droplet, group: 'Modules', keywords: 'stable funding asf rsf' },
  { id: 'forecast', label: 'Liquidity — Cash Flow Forecast (LSTM)', href: '/liquidity/forecast', icon: Droplet, group: 'Modules', keywords: 'ml prediction cashflow' },
  { id: 'stress', label: 'Liquidity — Stress Scenarios', href: '/liquidity/stress', icon: Droplet, group: 'Modules', keywords: 'runoff survival horizon' },
  { id: 'submission', label: 'Liquidity — Official Returns (BSD3)', href: '/submissions/returns?code=BSD3', icon: Droplet, group: 'Modules', keywords: 'regulatory filing return bog' },

  { id: 'fx', label: 'FX Risk — Net Open Position', hint: 'Module 03', href: '/fx', icon: DollarSign, group: 'Modules', keywords: 'nop currency exposure usd gbp eur cedi' },

  { id: 'basel', label: 'Basel Capital — Capital Dashboard', hint: 'Module 04', href: '/basel', icon: ShieldCheck, group: 'Modules', keywords: 'car cet1 tier capital adequacy' },
  { id: 'rwa', label: 'Basel — RWA Breakdown', href: '/basel/rwa', icon: ShieldCheck, group: 'Modules', keywords: 'risk weighted assets credit operational market' },
  { id: 'capital-structure', label: 'Basel — Capital Structure', href: '/basel/structure', icon: ShieldCheck, group: 'Modules', keywords: 'tier 1 2 cet1 instruments' },
  { id: 'basel-stress', label: 'Basel — Stress Testing', href: '/basel/stress', icon: ShieldCheck, group: 'Modules', keywords: 'scenario shock adverse' },
  { id: 'basel-subs', label: 'Basel — Official Returns (BSD2)', href: '/submissions/returns?code=BSD2', icon: ShieldCheck, group: 'Modules', keywords: 'regulatory filing return bog' },

  { id: 'ftp', label: 'FTP — Funds Transfer Pricing Dashboard', hint: 'Module 05', href: '/ftp', icon: GitBranch, group: 'Modules', keywords: 'margin spread pool matched maturity' },

  { id: 'forecasting', label: 'Forecasting — Balance Sheet Dashboard', hint: 'Module 06', href: '/forecasting', icon: TrendingUp, group: 'Modules', keywords: 'projection growth plan' },
  { id: 'forecast-scenario', label: 'Forecasting — Scenario Builder', href: '/forecasting/scenario', icon: TrendingUp, group: 'Modules', keywords: 'assumptions what if' },
  { id: 'forecast-optimizer', label: 'Forecasting — Strategy Optimizer', href: '/forecasting/optimizer', icon: TrendingUp, group: 'Modules', keywords: 'optimization mix' },
  { id: 'forecast-whatif', label: 'Forecasting — What-if Analysis', href: '/forecasting/whatif', icon: TrendingUp, group: 'Modules', keywords: 'sensitivity shock' },

  { id: 'behavioral', label: 'Behavioral Models', hint: 'Module 07', href: '/behavioral', icon: BrainCircuit, group: 'Modules', keywords: 'nmd core deposits prepayment decay ml' },

  { id: 'data-engine', label: 'Data Engine — Overview', hint: 'Module 00', href: '/data-engine', icon: Database, group: 'Data', keywords: 'ingestion integration upload' },
  { id: 'de-excel-csv', label: 'Data Engine — Excel & CSV', href: '/data-engine/excel-csv', icon: Database, group: 'Data', keywords: 'upload spreadsheet import' },
  { id: 'de-api', label: 'Data Engine — API Push', href: '/data-engine/api', icon: Database, group: 'Data', keywords: 'rest integration webhook token' },
  { id: 'de-t24', label: 'Data Engine — Temenos T24', href: '/data-engine/t24', icon: Database, group: 'Data', keywords: 'core banking adapter' },
  { id: 'de-adapters', label: 'Data Engine — Other adapters', href: '/data-engine/adapters', icon: Database, group: 'Data', keywords: 'flexcube finacle rubikon connector' },
  { id: 'de-canonical', label: 'Data Engine — Canonical Data', href: '/data-engine/positions', icon: Database, group: 'Data', keywords: 'positions facts tables' },
  { id: 'de-market', label: 'Data Engine — Market Data', href: '/data-engine/market-data', icon: Database, group: 'Data', keywords: 'rates feeds sources bog' },

  { id: 'reports', label: 'Reports Library', href: '/reports', icon: FileBarChart2, group: 'Governance', keywords: 'export pdf board pack' },
  { id: 'submissions', label: 'Regulatory Reporting', href: '/submissions', icon: FileCheck2, group: 'Governance', keywords: 'bog filing returns calendar approvals templates' },
  { id: 'settings', label: 'Settings', href: '/settings', icon: Settings, group: 'Governance', keywords: 'configuration limits thresholds admin' },
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
    // Every whitespace-separated term must match somewhere in the item's
    // label / hint / group / keywords haystack (simple fuzzy AND-filter).
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return items.filter((it) => {
      const haystack = `${it.label} ${it.hint ?? ''} ${it.group} ${
        it.keywords ?? ''
      }`.toLowerCase();
      return terms.every((t) => haystack.includes(t));
    });
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
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-xl bg-surface-raised border border-border rounded-lg shadow-pop overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border-light">
          <Search size={16} className="text-slate" aria-hidden />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search modules, screens, reports…"
            className="flex-1 bg-transparent outline-none text-body text-navy placeholder:text-slate"
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
