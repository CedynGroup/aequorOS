'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { KeyRound, Loader2 } from 'lucide-react';
import {
  ApiError,
  apiHost,
  clearToken,
  getHealth,
  listTenants,
  setToken,
  toApiError,
} from '@/lib/api';
import { Chip } from '@/components/ui';

/**
 * Dev-mode operator sign-in: a single bearer-token field. The token is stored
 * in sessionStorage and verified against GET /operator/v1/tenants (a 401
 * clears it and shows the error).
 *
 * NOTE: OIDC workforce login (staff IdP) replaces this page later. The token
 * path stays for local dev — do not build anything on top of this page that
 * assumes it is permanent.
 */
export default function LoginPage() {
  const router = useRouter();
  const [token, setTokenInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [health, setHealth] = useState<'checking' | 'ok' | 'unreachable'>('checking');

  // Unauthenticated reachability probe so a dead API is diagnosed before the
  // operator wonders why their token "doesn't work".
  useEffect(() => {
    let alive = true;
    getHealth()
      .then(() => alive && setHealth('ok'))
      .catch(() => alive && setHealth('unreachable'));
    return () => {
      alive = false;
    };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setToken(trimmed);
    try {
      await listTenants(); // verification call — any 2xx proves the token
      router.replace('/tenants');
    } catch (err) {
      clearToken();
      setError(toApiError(err));
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="text-h1 text-navy">AequorOS</div>
          <div className="text-micro uppercase tracking-widest text-slate">Operator Console</div>
        </div>

        <form onSubmit={submit} className="card space-y-4 p-6">
          <div className="flex items-center justify-between">
            <h1 className="text-h3 text-navy">Sign in</h1>
            {health === 'checking' && <Chip>probing API…</Chip>}
            {health === 'ok' && <Chip tone="ok">API reachable</Chip>}
            {health === 'unreachable' && (
              <Chip tone="crit" title={`GET ${apiHost()}/operator/health failed`}>
                API unreachable
              </Chip>
            )}
          </div>

          <label className="block">
            <span className="mb-1 block text-caption font-medium text-slate">
              Operator token — dev auth
            </span>
            <div className="flex items-center gap-2 rounded-md border border-border bg-surface-base px-3 py-2 focus-within:border-focus">
              <KeyRound size={14} className="shrink-0 text-slate" />
              <input
                type="password"
                autoComplete="off"
                autoFocus
                value={token}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="Paste bearer token"
                className="w-full bg-transparent font-mono text-body text-ink placeholder:text-slate-light focus:outline-none"
              />
            </div>
          </label>

          {error && (
            <p className="text-caption text-critical" role="alert">
              <span className="font-mono">{error.code}</span>
              {' · '}
              {error.status === 401
                ? 'Token rejected by the operator API.'
                : error.message}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !token.trim()}
            className="btn-primary flex w-full items-center justify-center gap-2 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {busy ? 'Verifying token…' : 'Sign in'}
          </button>

          <p className="text-micro text-slate-light">
            Token is held in sessionStorage for this tab only and verified against{' '}
            <span className="font-mono">{apiHost()}</span>. OIDC workforce login replaces this
            page; the token path stays for local dev.
          </p>
        </form>
      </div>
    </main>
  );
}
