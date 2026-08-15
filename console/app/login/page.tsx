'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Fraunces } from 'next/font/google';
import BrandLogo from '@/components/BrandLogo';
import { ArrowRight, KeyRound, Loader2, ShieldCheck } from 'lucide-react';
import {
  ApiError,
  clearToken,
  getAuthConfig,
  getHealth,
  listTenants,
  passwordLogin,
  setToken,
  toApiError,
  type AuthConfig,
} from '@/lib/api';

// Editorial display serif — the same family (and treatment) the client login
// leads with, so the staff surface carries the exact brand voice. Pattern
// copied from backend/dashboard/app/login/page.tsx; no dashboard imports.
const fraunces = Fraunces({ subsets: ['latin'], weight: ['500'], display: 'swap' });

/**
 * Operator sign-in — the client login's model and design, replicated for
 * staff (founder's directive: "We are building the same as client"):
 * - Email + password (PRIMARY): POST /api/auth/password-login relays to the
 *   operator API server-side and sets the HttpOnly op_session cookie.
 * - Workforce SSO (secondary): the existing OIDC code+PKCE flow via
 *   /api/auth/login, shown only when the deployment configures OPERATOR_OIDC_*.
 * - Dev token: development builds only, behind a collapsed disclosure.
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

function passwordErrorMessage(error: ApiError): string {
  if (error.status === 401) return 'Invalid email or password.';
  // 429 (throttle) and 503 (OPERATOR_JWT_SECRET unset) carry precise backend
  // messages — surface them verbatim.
  if (error.status === 429 || error.status === 503) return error.message;
  // Anything else is the service failing, and must never be reported as a
  // bad password (the dashboard's 2026-07-26 lesson: telling someone their
  // credentials are invalid when the service is down sends them to reset a
  // password that was never checked).
  return (
    'Could not reach the operator service, so your sign-in could not be checked. ' +
    'This is not a problem with your credentials — try again shortly.'
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [health, setHealth] = useState<'checking' | 'ok' | 'unreachable'>('checking');

  // Dev-token disclosure state (development builds only).
  const [devToken, setDevToken] = useState('');
  const [devBusy, setDevBusy] = useState(false);
  const [devError, setDevError] = useState<ApiError | null>(null);

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

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await passwordLogin(email, password);
      router.replace('/tenants');
    } catch (err) {
      setError(passwordErrorMessage(toApiError(err)));
      setPending(false);
    }
  }

  async function onDevSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = devToken.trim();
    if (!trimmed || devBusy) return;
    setDevBusy(true);
    setDevError(null);
    setToken(trimmed);
    try {
      await listTenants(); // verification call — any 2xx proves the token
      router.replace('/tenants');
    } catch (err) {
      clearToken();
      setDevError(toApiError(err));
      setDevBusy(false);
    }
  }

  const oidc = config?.oidc_configured === true;

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr,minmax(0,520px)]">
      {/* Brand panel — editorial statement, one accent, one line of geometry
          (the client login's composition, in console tokens). */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-nav p-12 text-white lg:flex">
        <style>{`
          .lg-arc { stroke-dasharray: 1400; stroke-dashoffset: 1400; animation: lgArc 1.8s cubic-bezier(.22,.61,.36,1) .3s forwards; }
          .lg-arc-accent { stroke-dasharray: 190 1210; stroke-dashoffset: 1400; animation: lgTravel 16s linear .5s infinite; }
          @keyframes lgArc { to { stroke-dashoffset: 0; } }
          @keyframes lgTravel { from { stroke-dashoffset: 1400; } to { stroke-dashoffset: 0; } }
          @media (prefers-reduced-motion: reduce) {
            .lg-arc { animation: none; stroke-dashoffset: 0; }
            .lg-arc-accent { animation: none; stroke-dashoffset: 0; }
          }
        `}</style>

        <svg
          className="pointer-events-none absolute inset-y-0 right-0 h-full"
          viewBox="0 0 560 900"
          fill="none"
          aria-hidden
        >
          <path
            className="lg-arc"
            d="M560 40 A 640 640 0 0 0 60 900"
            stroke="rgba(255,255,255,0.09)"
            strokeWidth="1"
          />
          <path
            className="lg-arc-accent"
            d="M560 40 A 640 640 0 0 0 60 900"
            stroke="rgba(110,168,255,0.85)"
            strokeWidth="1.5"
          />
        </svg>

        <div>
        <BrandLogo inverse subtitle="Operator Console" />
        </div>

        <div className="relative max-w-2xl">
          <h1
            className={`${fraunces.className} text-[64px] leading-[1.04] tracking-tight xl:text-[80px]`}
          >
            Onboarding<span className="text-action">.</span>
            <br />
            Operations<span className="text-action">.</span>
            <br />
            Markets<span className="text-action">.</span>
          </h1>
          <p className="mt-8 max-w-md text-body-lg leading-relaxed text-white/55">
            One console for onboarding, operations, and the market research
            desk.
          </p>
        </div>

        <p className="relative text-caption text-white/35">
          © {new Date().getFullYear()} AequorOS, Inc. · Internal staff tooling
        </p>
      </div>

      {/* Sign-in panel — calm, minimal, one primary action. */}
      <div className="flex items-center justify-center bg-surface-raised p-8">
        <div className="w-full max-w-sm">
          <div className="mb-10 lg:hidden">
            <BrandLogo subtitle="Operator Console" />
          </div>

          <h2 className="text-h1 text-navy">Sign in</h2>
          <p className="mt-2 text-body leading-relaxed text-slate">
            {oidc
              ? 'Use your operator credentials, or workforce single sign-on.'
              : 'Use your operator credentials.'}
          </p>

          {health === 'unreachable' && (
            <p
              role="status"
              className="mt-4 rounded-md border border-border bg-surface-base px-3 py-2.5 text-caption leading-relaxed text-warning"
            >
              The operator API{config?.api_host ? ` at ${config.api_host}` : ''} is
              unreachable — sign-in cannot be checked until it is back.
            </p>
          )}

          {ssoError && (
            <p role="alert" className="mt-4 text-caption text-critical">
              <span className="font-mono">{ssoError}</span>
              {' · '}
              {CALLBACK_ERRORS[ssoError] ?? 'Workforce sign-in failed — try again.'}
            </p>
          )}

          <form onSubmit={onSubmit} className="mt-8 space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-caption font-medium text-navy">Email</span>
              <input
                type="email"
                required
                autoComplete="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border border-border bg-surface-base px-3 py-2.5 text-body text-ink focus:border-focus focus:outline-none"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 block text-caption font-medium text-navy">Password</span>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-border bg-surface-base px-3 py-2.5 text-body text-ink focus:border-focus focus:outline-none"
              />
            </label>

            {error ? (
              <p role="alert" className="text-caption leading-relaxed text-critical">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={pending}
              className="btn-primary inline-flex w-full items-center justify-center gap-2 px-4 py-3 text-body font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            >
              {pending ? 'Signing in…' : 'Sign in'}
              {pending ? (
                <Loader2 size={16} className="animate-spin" aria-hidden />
              ) : (
                <ArrowRight size={16} aria-hidden />
              )}
            </button>

            {oidc ? (
              <>
                <div className="relative py-2 text-center">
                  <span className="relative z-10 bg-surface-raised px-3 text-caption text-slate">
                    or
                  </span>
                  <span
                    className="absolute inset-x-0 top-1/2 border-t border-border-light"
                    aria-hidden
                  />
                </div>

                <a
                  href="/api/auth/login"
                  className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-border bg-surface-base px-4 py-3 text-body font-medium text-navy transition-colors hover:bg-surface"
                >
                  <ShieldCheck size={15} aria-hidden />
                  Sign in with workforce SSO
                </a>
                <p className="text-micro leading-relaxed text-slate-light">
                  Redirects to <span className="font-mono">{config?.issuer_host}</span>.
                  Your identity token is held server-side only; the operator API
                  re-verifies it on every request.
                </p>
              </>
            ) : null}
          </form>

          {process.env.NODE_ENV === 'development' && (
            <details className="mt-8">
              <summary className="cursor-pointer select-none text-micro uppercase tracking-wide text-slate-light">
                Local dev
              </summary>
              <form onSubmit={onDevSubmit} className="mt-3 space-y-3">
                <div className="flex items-center gap-2 rounded-md border border-border bg-surface-base px-3 py-2 focus-within:border-focus">
                  <KeyRound size={14} className="shrink-0 text-slate" aria-hidden />
                  <input
                    type="password"
                    autoComplete="off"
                    value={devToken}
                    onChange={(e) => setDevToken(e.target.value)}
                    placeholder="Paste OPERATOR_DEV_TOKEN"
                    className="w-full bg-transparent font-mono text-body text-ink placeholder:text-slate-light focus:outline-none"
                  />
                </div>
                {devError && (
                  <p className="text-caption text-critical" role="alert">
                    <span className="font-mono">{devError.code}</span>
                    {' · '}
                    {devError.status === 401
                      ? 'Token rejected by the operator API.'
                      : devError.message}
                  </p>
                )}
                <button
                  type="submit"
                  disabled={devBusy || !devToken.trim()}
                  className="btn-secondary flex w-full items-center justify-center gap-2 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {devBusy && <Loader2 size={14} className="animate-spin" aria-hidden />}
                  {devBusy ? 'Verifying token…' : 'Sign in with dev token'}
                </button>
                <p className="text-micro leading-relaxed text-slate-light">
                  Development builds only. The token lives in sessionStorage for this
                  tab and the backend refuses it when{' '}
                  <span className="font-mono">APP_ENV=production</span>.
                </p>
              </form>
            </details>
          )}
        </div>
      </div>
    </div>
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
