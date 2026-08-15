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
  Loader2,
  LogOut,
  UserRound,
} from 'lucide-react';
import { useSession, signOut } from 'next-auth/react';
import { useBankContext } from './BankContext';
import { useTheme } from './ThemeProvider';
import { fmtDateUTC, fmtRelative, isoDate } from '@/lib/api/values';
import { avatarColor, initialsFrom, roleLabel } from '@/lib/api/identity';
import { useUserProfile } from '@/components/profile/ProfileProvider';
import { LOGIN_URL } from '@/lib/loginUrl';
import { useLiveSummary, useRefreshBankData } from '@/lib/api/hooks';
import CommandPalette from './CommandPalette';
import UnifiedBell from '@/components/shell/UnifiedBell';
import { regShort } from '@/lib/format';
import { useImpersonation } from '@/components/impersonation/useImpersonation';
import { leaveImpersonation } from '@/lib/api/impersonation';

export default function Header({
  onMobileMenu,
}: {
  onMobileMenu?: () => void;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { bank } = useBankContext();
  const { impersonating } = useImpersonation();

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
    // While inspecting, drop the sticky header below the fixed staff banner
    // (2.5rem) so the two never overlap. No offset on a normal session.
    <header
      style={impersonating ? { top: '2.5rem' } : undefined}
      className="h-16 bg-surface-raised border-b border-border-light flex items-center justify-between px-4 md:px-6 sticky top-0 z-30"
    >
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
            {bank ? `${regShort()} license · ${capitalize(bank.licenseType)}` : '—'}
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

        <UnifiedBell />

        <ThemeToggle />

        <UserMenu />
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </header>
  );
}


/** How old the live tier may be before the header stops calling it current.
 * The scheduled refresh runs hourly and every accepted ingestion batch also
 * triggers one, so anything past two hours means the pipeline is not running. */
const LIVE_STALE_AFTER_MS = 2 * 60 * 60 * 1000;

/** Compact live-engine recency pill: dot + "Live · 2m ago".
 *
 * State is the AGE of the live tier, never the comparison against official
 * runs — official runs are a Governance concern (the moat), and the desk
 * surfaces live-compute continuously. Amber here means only "the live tier
 * has not recomputed recently"; the click is a manual recompute. */
function LiveFreshnessPill() {
  const { bank, period } = useBankContext();
  const { impersonating } = useImpersonation();
  const summary = useLiveSummary(bank?.id);
  const refresh = useRefreshBankData(bank?.id);

  const modules = summary.data?.modules ?? [];
  if (modules.length === 0) return null;

  // Read-only staff view: never surface the recompute action (a mutation the
  // backend would 403). Show a static liveness pill instead of a dead button.
  if (impersonating) {
    return (
      <span
        title="Live figures — read-only staff inspection."
        className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1 mx-1 rounded-full border border-success/30 bg-success-light text-success text-caption font-medium whitespace-nowrap"
      >
        <RadioTower size={11} aria-hidden />
        Live
      </span>
    );
  }

  const latest = modules.reduce<Date | null>((acc, m) => {
    if (!m.computedAt) return acc;
    const computed = new Date(m.computedAt);
    return !acc || computed.getTime() > acc.getTime() ? computed : acc;
  }, null);
  const aged = !latest || Date.now() - latest.getTime() > LIVE_STALE_AFTER_MS;

  if (aged || refresh.isPending) {
    return (
      <button
        type="button"
        disabled={refresh.isPending || !period}
        onClick={() =>
          period &&
          refresh.mutate({
            asOfDate: isoDate(period.periodEnd),
            reason: 'Recompute live figures (header)',
          })
        }
        title="The live tier has not recomputed recently — click to recompute now. It refreshes automatically as data arrives and on the hourly schedule."
        className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1 mx-1 rounded-full border border-warning/30 bg-warning-light text-warning text-caption font-medium whitespace-nowrap hover:bg-warning/15 disabled:opacity-60"
      >
        {refresh.isPending ? (
          <Loader2 size={11} className="animate-spin" aria-hidden />
        ) : (
          <AlertTriangle size={11} aria-hidden />
        )}
        {refresh.isPending
          ? 'Recomputing…'
          : `Live · stale${latest ? ` (${fmtRelative(latest)})` : ''}`}
      </button>
    );
  }

  return (
    <span
      title={`Live figures — recomputed automatically as data arrives and on the hourly schedule${latest ? `; engines last ran ${fmtRelative(latest)}` : ''}.`}
      className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1 mx-1 rounded-full border border-success/30 bg-success-light text-success text-caption font-medium whitespace-nowrap"
    >
      <RadioTower size={11} aria-hidden />
      Live
    </span>
  );
}

/** Dark / light theme switch. */
function ThemeToggle() {
  const { resolvedTheme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${resolvedTheme === 'dark' ? 'light' : 'dark'} theme`}
      title={`Switch to ${resolvedTheme === 'dark' ? 'light' : 'dark'} theme`}
      className="w-9 h-9 inline-flex items-center justify-center rounded text-slate hover:bg-surface hover:text-navy transition-colors"
    >
      {resolvedTheme === 'dark' ? (
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
  const { profile } = useUserProfile();
  const { impersonating, operator } = useImpersonation();

  // While inspecting there is no tenant session; show the operator's identity
  // and a "read-only" role so the avatar menu is never a confusing blank.
  const email = impersonating
    ? (operator ?? '')
    : (profile?.email ?? session?.user?.email ?? '');
  const roles = session?.user?.roles ?? [];
  const name = impersonating
    ? (operator ?? 'AequorOS staff')
    : profile?.displayName || session?.user?.name || email || 'Signed in';
  const role = impersonating
    ? 'Staff inspection · read-only'
    : profile?.role
      ? roleLabel(profile.role)
      : roles.length
        ? roleLabel(roles[0])
        : 'Signed in';
  const initials = initialsFrom(name);
  const avatarBackground = avatarColor(
    impersonating ? (operator ?? 'AequorOS staff') : (profile?.userId ?? email),
  );

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
        <span
          className="inline-flex items-center justify-center w-8 h-8 rounded-full text-white text-caption font-semibold shrink-0"
          style={{ backgroundColor: avatarBackground }}
        >
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
            {impersonating ? (
              // Read-only staff view: no profile edits (they would 403), and
              // "sign out" is meaningless with no tenant session — leaving the
              // inspection is the only exit.
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  void leaveImpersonation().then(() => {
                    window.location.href = LOGIN_URL;
                  });
                }}
                className="w-full flex items-center gap-2.5 px-4 py-2 text-body text-navy/85 hover:bg-surface"
              >
                <LogOut size={14} className="text-slate" aria-hidden />
                Leave inspection
              </button>
            ) : (
              <>
                <a
                  href="/settings/profile"
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
              </>
            )}
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
        title="Reporting period for analysis and governance — desk figures always track the live edge of the newest period."
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded text-caption font-medium text-slate hover:bg-surface"
      >
        <Calendar size={13} aria-hidden />
        Period{' '}
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
