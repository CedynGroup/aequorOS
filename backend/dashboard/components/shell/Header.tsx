'use client';

import { useState, useEffect, useRef } from 'react';
import {
  Search,
  ChevronDown,
  Calendar,
  Check,
  Menu,
  Sun,
  Moon,
  RadioTower,
  AlertTriangle,
  LogOut,
  UserRound,
} from 'lucide-react';
import { useSession, signOut } from 'next-auth/react';
import { useBankContext } from './BankContext';
import { useTheme } from './ThemeProvider';
import { fmtDateUTC, fmtRelative } from '@/lib/api/values';
import { initialsFrom, roleLabel } from '@/lib/api/identity';
import { LOGIN_URL } from '@/lib/loginUrl';
import { useBankFreshness } from '@/lib/api/hooks';
import CommandPalette from './CommandPalette';
import AlertsBell from '@/components/live/AlertsBell';
import { regShort } from '@/lib/format';

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
    <header className="h-16 bg-surface-raised border-b border-border-light flex items-center justify-between px-4 md:px-6 sticky top-0 z-30">
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
            {regShort()} license · {bank ? capitalize(bank.licenseType) : '—'}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="hidden md:inline-flex items-center gap-2 mr-1 bg-surface border border-border-light rounded-md pl-3 pr-2 py-1.5 text-caption text-slate hover:border-action/40 hover:text-navy transition-colors w-56"
        >
          <Search size={13} aria-hidden className="shrink-0" />
          <span className="flex-1 truncate text-left">Search…</span>
          <kbd className="text-[10px] font-mono bg-surface-raised border border-border-light rounded px-1.5 py-0.5 shrink-0">
            ⌘K
          </kbd>
        </button>

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          aria-label="Search"
          className="md:hidden w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
        >
          <Search size={16} aria-hidden />
        </button>

        <PeriodSelector />

        <LiveFreshnessPill />

        <AlertsBell />

        <ThemeToggle />

        <UserMenu />
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </header>
  );
}

/** Compact aggregate live-engine freshness pill: dot + "Live · 2m ago". */
function LiveFreshnessPill() {
  const { bank, period } = useBankContext();
  const freshness = useBankFreshness(bank?.id, period?.id);

  const modules = freshness.data?.modules ?? [];
  if (modules.length === 0) return null;

  const anyStale = modules.some((m) => m.isStale);
  const latest = modules.reduce<Date | null>((acc, m) => {
    if (!m.computedAt) return acc;
    const computed = new Date(m.computedAt);
    return !acc || computed.getTime() > acc.getTime() ? computed : acc;
  }, null);

  if (anyStale) {
    return (
      <span
        title="Data changed since the last official run — mint one from the module dashboard."
        className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1 mx-1 rounded-full border border-warning/30 bg-warning-light text-warning text-caption font-medium whitespace-nowrap"
      >
        <AlertTriangle size={11} aria-hidden />
        Changed
      </span>
    );
  }

  return (
    <span
      title="Live figures match the last official run."
      className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1 mx-1 rounded-full border border-success/30 bg-success-light text-success text-caption font-medium whitespace-nowrap"
    >
      <RadioTower size={11} aria-hidden />
      Live{latest ? ` · ${fmtRelative(latest)}` : ''}
    </span>
  );
}

/** Dark / light theme switch. */
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      className="w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface hover:text-navy transition-colors"
    >
      {theme === 'dark' ? (
        <Sun size={16} aria-hidden />
      ) : (
        <Moon size={16} aria-hidden />
      )}
    </button>
  );
}

/** Signed-in user avatar menu, backed by the NextAuth session. */
function UserMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { data: session } = useSession();

  const email = session?.user?.email ?? '';
  const name = session?.user?.name || email || 'Signed in';
  const roles = session?.user?.roles ?? [];
  const role = roles.length ? roleLabel(roles[0]) : 'Signed in';
  const initials = initialsFrom(name);

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
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 ml-2 px-2 py-1.5 rounded hover:bg-surface"
      >
        <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-teal text-white text-caption font-semibold shrink-0">
          {initials}
        </span>
        <span className="hidden lg:block text-left">
          <span className="block text-caption font-medium text-navy leading-tight max-w-[12rem] truncate">
            {name}
          </span>
          <span className="block text-[10px] text-slate leading-tight">
            {role}
          </span>
        </span>
        <ChevronDown size={12} className="hidden lg:block text-slate" aria-hidden />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="User menu"
          className="absolute right-0 mt-1.5 w-60 bg-surface-raised border border-border-light rounded-md shadow-pop z-40 overflow-hidden"
        >
          <div className="px-4 py-3 border-b border-border-light">
            <p className="text-body font-medium text-navy truncate">{name}</p>
            <p className="text-caption text-slate truncate">
              {email ? `${email} · ${role}` : role}
            </p>
          </div>
          <div className="py-1">
            <a
              href="/settings"
              role="menuitem"
              className="flex items-center gap-2.5 px-4 py-2 text-body text-navy/85 hover:bg-surface"
              onClick={() => setOpen(false)}
            >
              <UserRound size={14} className="text-slate" aria-hidden />
              Profile &amp; preferences
            </a>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void signOut({ redirectTo: LOGIN_URL });
              }}
              className="w-full flex items-center gap-2.5 px-4 py-2 text-body text-navy/85 hover:bg-surface"
            >
              <LogOut size={14} className="text-slate" aria-hidden />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
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
        <span className="font-mono text-navy tnum">
          {period ? fmtDateUTC(period.periodEnd) : '—'}
        </span>
        <ChevronDown size={12} aria-hidden />
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Reporting period"
          className="absolute right-0 mt-1 w-56 max-h-80 overflow-y-auto bg-surface-raised border border-border-light rounded-md shadow-pop py-1 z-40"
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
                    <span className="ml-2 font-mono text-[10px] text-slate tnum">
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
