'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { KeyRound, Loader2, ShieldCheck } from 'lucide-react';
import {
  ApiError,
  clearToken,
  getAuthConfig,
  getHealth,
  listTenants,
  setToken,
  toApiError,
  type AuthConfig,
} from '@/lib/api';
import { Chip } from '@/components/ui';

/**
 * Operator sign-in, two paths:
 * - Workforce SSO (primary): full OIDC authorization-code + PKCE flow via
 *   /api/auth/login → IdP → /api/auth/callback. The id_token lands in an
 *   HttpOnly cookie; browser JS never sees it. Shown whenever the deployment
 *   configures OPERATOR_OIDC_* on the console.
 * - Dev token (local dev only): sessionStorage bearer verified against
 *   GET /operator/v1/tenants. The backend hard-refuses dev auth when
 *   APP_ENV=production, so this path is inert in real deployments.
 */

const CALLBACK_ERRORS: Record<string, string> = {
  oidc_not_configured: 'Workforce SSO is not configured on this console.',
  idp_unreachable: 'The identity provider could not be reached.',
  missing_transaction: 'The sign-in attempt expired — try again.',
  state_mismatch: 'Sign-in state check failed — try again.',
  token_exchange_failed: 'The identity provider rejected the sign-in.',
  no_id_token: 'The identity provider returned no id_token.',
  malformed_id_token: 'The identity provider returned an unreadable token.',
  nonce_mismatch: 'Sign-in nonce check failed — try again.',
  no_email_claim: 'Your IdP account has no email claim.',
  expired: 'The identity provider returned an already-expired token.',
};

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [token, setTokenInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [health, setHealth] = useState<'checking' | 'ok' | 'unreachable'>('checking');

  const ssoError = searchParams.get('error');

  // Unauthenticated reachability probe so a dead API is diagnosed before the
  // operator wonders why their sign-in "doesn't work".
  useEffect(() => {
    let alive = true;
    getHealth()
      .then(() => alive && setHealth('ok'))
      .catch(() => alive && setHealth('unreachable'));
    getAuthConfig()
      .then((c) => alive && setConfig(c))
      .catch(() => alive && setConfig(null));
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

  const oidc = config?.oidc_configured === true;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="text-h1 text-navy">AequorOS</div>
          <div className="text-micro uppercase tracking-widest text-slate">Operator Console</div>
        </div>

        <div className="card space-y-4 p-6">
          <div className="flex items-center justify-between">
            <h1 className="text-h3 text-navy">Sign in</h1>
            {health === 'checking' && <Chip>probing API…</Chip>}
            {health === 'ok' && <Chip tone="ok">API reachable</Chip>}
            {health === 'unreachable' && (
              <Chip tone="crit" title={`GET ${config?.api_host ?? 'operator API'}/operator/health failed`}>
                API unreachable
              </Chip>
            )}
          </div>

          {ssoError && (
            <p className="text-caption text-critical" role="alert">
              <span className="font-mono">{ssoError}</span>
              {' · '}
              {CALLBACK_ERRORS[ssoError] ?? 'Workforce sign-in failed — try again.'}
            </p>
          )}

          {oidc && (
            <>
              <a
                href="/api/auth/login"
                className="btn-primary flex w-full items-center justify-center gap-2 px-4 py-2 text-body font-medium"
              >
                <ShieldCheck size={15} />
                Sign in with workforce SSO
              </a>
              <p className="text-micro text-slate-light">
                Redirects to <span className="font-mono">{config?.issuer_host}</span>. Your
                identity token is held server-side only; the operator API re-verifies it on
                every request.
              </p>
              <div className="flex items-center gap-3">
                <div className="h-px flex-1 bg-border-light" />
                <span className="text-micro uppercase tracking-wide text-slate-light">
                  or local dev
                </span>
                <div className="h-px flex-1 bg-border-light" />
              </div>
            </>
          )}

          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <span className="mb-1 block text-caption font-medium text-slate">
                Operator token — dev auth
              </span>
              <div className="flex items-center gap-2 rounded-md border border-border bg-surface-base px-3 py-2 focus-within:border-focus">
                <KeyRound size={14} className="shrink-0 text-slate" />
                <input
                  type="password"
                  autoComplete="off"
                  autoFocus={!oidc}
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
                {error.status === 401 ? 'Token rejected by the operator API.' : error.message}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || !token.trim()}
              className={`${oidc ? 'btn-secondary' : 'btn-primary'} flex w-full items-center justify-center gap-2 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50`}
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              {busy ? 'Verifying token…' : 'Sign in with dev token'}
            </button>
          </form>

          <p className="text-micro text-slate-light">
            The dev token is held in sessionStorage for this tab only and is refused by the
            backend when <span className="font-mono">APP_ENV=production</span> — workforce SSO
            is the only production path.
          </p>
        </div>
      </div>
    </main>
  );
}

export default function LoginPage() {
  // useSearchParams requires a Suspense boundary during prerender.
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
