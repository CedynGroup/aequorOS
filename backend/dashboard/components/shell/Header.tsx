'use client';

import { useState, useEffect, useRef } from 'react';
import { Search, ChevronDown, Calendar, Check, Menu } from 'lucide-react';
import { useBankContext } from './BankContext';
import { fmtDateUTC } from '@/lib/api/values';
import CommandPalette from './CommandPalette';
import AlertsBell from '@/components/live/AlertsBell';

// Static UI persona for the demo shell — chrome, not financial data.
const treasurer = {
  name: 'Akua Mensah',
  role: 'Head of Treasury & ALM',
  initials: 'AM',
};

export default function Header({
  onMobileMenu,
}: {
  onMobileMenu?: () => void;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { bank } = useBankContext();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <header className="h-16 bg-white border-b border-border-light flex items-center justify-between px-4 md:px-6 sticky top-0 z-30">
      <div className="flex items-center gap-3 min-w-0">
        {onMobileMenu && (
          <button
            type="button"
            onClick={onMobileMenu}
            aria-label="Open menu"
            className="lg:hidden w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
          >
            <Menu size={18} aria-hidden />
          </button>
        )}
        <div className="text-body min-w-0">
          <span className="font-medium text-navy truncate">
            {bank?.name ?? '—'}
          </span>
          <span className="hidden md:inline mx-2 text-slate-light">|</span>
          <span className="hidden md:inline text-slate text-caption">
            BoG license · {bank ? capitalize(bank.licenseType) : '—'}
          </span>
        </div>
      </div>

      <div className="hidden md:flex flex-1 max-w-md mx-8">
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="w-full inline-flex items-center gap-2 bg-surface border border-border-light rounded-md pl-3 pr-2 py-2 text-body text-slate hover:border-action/40 hover:text-navy transition-colors text-left"
        >
          <Search size={14} aria-hidden className="shrink-0" />
          <span className="flex-1 truncate">Search modules, screens, reports…</span>
          <kbd className="text-[10px] font-mono bg-white border border-border-light rounded px-1.5 py-0.5 shrink-0">
            ⌘K
          </kbd>
        </button>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Search"
          className="md:hidden w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
        >
          <Search size={16} aria-hidden />
        </button>

        <PeriodSelector />

        <AlertsBell />

        <button
          type="button"
          className="inline-flex items-center gap-2 ml-2 px-2 py-1.5 rounded hover:bg-surface"
        >
          <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-teal text-white text-caption font-semibold shrink-0">
            {treasurer.initials}
          </span>
          <span className="hidden lg:block text-left">
            <span className="block text-caption font-medium text-navy leading-tight">
              {treasurer.name}
            </span>
            <span className="block text-[10px] text-slate leading-tight">
              {treasurer.role}
            </span>
          </span>
          <ChevronDown size={12} className="hidden lg:block text-slate" aria-hidden />
        </button>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </header>
  );
}

/** Real reporting-period selector backed by the risk service. */
function PeriodSelector() {
  const { period, periods, setPeriodId } = useBankContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative hidden md:block" ref={ref}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded text-caption font-medium text-slate hover:bg-surface"
      >
        <Calendar size={13} aria-hidden />
        As of{' '}
        <span className="font-mono text-navy">
          {period ? fmtDateUTC(period.periodEnd) : '—'}
        </span>
        <ChevronDown size={12} aria-hidden />
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Reporting period"
          className="absolute right-0 mt-1 w-56 max-h-80 overflow-y-auto bg-white border border-border-light rounded-md shadow-lg py-1 z-40"
        >
          {periods.map((p) => {
            const selected = p.id === period?.id;
            return (
              <li key={p.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setPeriodId(p.id);
                    setOpen(false);
                  }}
                  className={`w-full flex items-center justify-between gap-3 px-3 py-2 text-caption text-left hover:bg-surface ${
                    selected ? 'text-navy font-medium' : 'text-slate'
                  }`}
                >
                  <span>
                    {p.label}
                    <span className="ml-2 font-mono text-[10px] text-slate">
                      {fmtDateUTC(p.periodEnd)}
                    </span>
                  </span>
                  {selected && (
                    <Check size={12} className="text-action shrink-0" aria-hidden />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
