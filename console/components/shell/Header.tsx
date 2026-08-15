'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronDown, LogOut, Search } from 'lucide-react';
import {
  clearToken,
  getAuthConfig,
  getToken,
  getWorkforceSession,
  workforceLogout,
} from '@/lib/api';
import CommandPalette from './CommandPalette';

type Identity =
  | { mode: 'oidc' | 'password'; email: string }
  | { mode: 'dev' }
  | null;

/**
 * Console top bar (h-16): a ⌘K search button that opens the CommandPalette, an
 * API-host badge (which operator API this console proxies to — the single most
 * important operator context), the session identity, and a user menu carrying
 * sign-out. The Inspector banner renders as its own strip below this header
 * (AppShell).
 */
export default function Header() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [apiHost, setApiHost] = useState<string | null>(null);
  const [identity, setIdentity] = useState<Identity>(null);

  useEffect(() => {
    let alive = true;
    getAuthConfig()
      .then((config) => alive && setApiHost(config.api_host))
      .catch(() => alive && setApiHost(null));
    if (getToken()) {
      setIdentity({ mode: 'dev' });
    } else {
      getWorkforceSession()
        .then(
          (session) =>
            alive &&
            setIdentity(
              session.authenticated && session.email
                ? {
                    mode: session.mode === 'password' ? 'password' : 'oidc',
                    email: session.email,
                  }
                : null,
            ),
        )
        .catch(() => alive && setIdentity(null));
    }
    return () => {
      alive = false;
    };
  }, []);

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
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-border-light bg-surface-raised px-4 md:px-6">
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="inline-flex w-72 max-w-[40vw] items-center gap-2 rounded-md border border-border-light bg-surface py-1.5 pl-3 pr-2 text-caption text-slate transition-colors hover:border-action/40 hover:text-navy"
      >
        <Search size={13} aria-hidden className="shrink-0" />
        <span className="flex-1 truncate text-left">Search screens and tenants…</span>
        <kbd className="shrink-0 rounded border border-border-light bg-surface-raised px-1.5 py-0.5 font-mono text-[10px]">
          ⌘K
        </kbd>
      </button>

      <div className="flex items-center gap-2">
        {apiHost && (
          <span
            className="hidden rounded border border-border-light bg-surface px-2 py-0.5 font-mono text-micro text-slate md:inline"
            title={`Operator API base (via the console's /api/op proxy): ${apiHost}`}
          >
            API · {apiHost}
          </span>
        )}
        {identity?.mode === 'dev' && (
          <span className="rounded bg-warning-light px-2 py-0.5 text-micro font-medium uppercase tracking-wide text-warning">
            dev session
          </span>
        )}
        <UserMenu identity={identity} />
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </header>
  );
}

function initialsFor(email: string): string {
  const local = email.split('@')[0] ?? email;
  const parts = local.split(/[.\-_]+/).filter(Boolean);
  const chars = parts.length >= 2 ? `${parts[0][0]}${parts[1][0]}` : local.slice(0, 2);
  return chars.toUpperCase();
}

function UserMenu({ identity }: { identity: Identity }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
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

  async function signOut() {
    clearToken();
    try {
      await workforceLogout();
    } catch {
      // Cookie clearing failed (console route unreachable) — the redirect to
      // /login still ends the usable session; the cookie dies with the token exp.
    }
    router.replace('/login');
  }

  const email =
    identity && identity.mode !== 'dev' ? identity.email : identity?.mode === 'dev' ? 'Dev session' : 'Signed in';
  const modeLabel =
    identity?.mode === 'password'
      ? 'Operator · password'
      : identity?.mode === 'oidc'
      ? 'Operator · SSO'
      : identity?.mode === 'dev'
      ? 'Local dev token'
      : '';
  const initials = identity && identity.mode !== 'dev' ? initialsFor(identity.email) : 'OP';

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded px-1.5 py-1 hover:bg-surface"
      >
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-action-light text-caption font-semibold text-action">
          {initials}
        </span>
        <span className="hidden text-left lg:block">
          <span className="block max-w-[14rem] truncate text-caption font-medium leading-tight text-navy">
            {email}
          </span>
          {modeLabel && (
            <span className="block text-[10px] leading-tight text-slate">{modeLabel}</span>
          )}
        </span>
        <ChevronDown size={12} className="hidden text-slate lg:block" aria-hidden />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="User menu"
          className="absolute right-0 z-40 mt-1.5 w-60 overflow-hidden rounded-md border border-border-light bg-surface-raised shadow-pop"
        >
          <div className="border-b border-border-light px-4 py-3">
            <p className="truncate text-body font-medium text-navy">{email}</p>
            {modeLabel && <p className="truncate text-caption text-slate">{modeLabel}</p>}
          </div>
          <div className="py-1">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void signOut();
              }}
              className="flex w-full items-center gap-2.5 px-4 py-2 text-body text-navy/85 hover:bg-surface"
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
