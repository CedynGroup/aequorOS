'use client';

import { useState, useEffect } from 'react';
import { Bell, Search, ChevronDown, Calendar, Menu } from 'lucide-react';
import { bank, treasurer } from '@/lib/data/bank';
import CommandPalette from './CommandPalette';
import NotificationDrawer from './NotificationDrawer';

export default function Header({
  onMobileMenu,
}: {
  onMobileMenu?: () => void;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);

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
          <span className="font-medium text-navy truncate">{bank.name}</span>
          <span className="hidden md:inline mx-2 text-slate-light">|</span>
          <span className="hidden md:inline text-slate text-caption">
            BoG license · {bank.licenseClass}
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

        <button
          type="button"
          className="hidden md:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded text-caption font-medium text-slate hover:bg-surface"
        >
          <Calendar size={13} aria-hidden />
          As of <span className="font-mono text-navy">{bank.asOf}</span>
          <ChevronDown size={12} aria-hidden />
        </button>

        <button
          type="button"
          aria-label="Notifications"
          onClick={() => setNotifOpen(true)}
          className="relative w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
        >
          <Bell size={16} aria-hidden />
          <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-action ring-2 ring-white" />
        </button>

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
      <NotificationDrawer open={notifOpen} onClose={() => setNotifOpen(false)} />
    </header>
  );
}
