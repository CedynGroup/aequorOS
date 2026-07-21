import { Suspense } from 'react';
import { Fraunces } from 'next/font/google';
import Logo from '@/components/shell/Logo';
import LoginForm from './LoginForm';

// Editorial display serif — the same family the marketing site leads with, so
// the brand voice carries from aequoros.com into the product.
const fraunces = Fraunces({ subsets: ['latin'], weight: ['500'], display: 'swap' });

// Rendered per request: the SSO button reflects the org's live connection state
// (Settings → Authentication), not a build-time snapshot.
export const dynamic = 'force-dynamic';

const apiOrigin = (process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? 'http://localhost:8000')
  .replace(/\/api\/v1\/?$/, '');

/** Public probe: is an SSO connection enabled? Backend down → just hide the button. */
async function ssoEnabled(): Promise<boolean> {
  try {
    const res = await fetch(`${apiOrigin}/api/v1/auth/sso/status`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return false;
    return ((await res.json()) as { enabled: boolean }).enabled;
  } catch {
    return false;
  }
}

export default async function LoginPage() {
  const withSso = await ssoEnabled();
  return (
    <>
      <div className="min-h-screen grid lg:grid-cols-[1.1fr,minmax(0,520px)]">
        {/* Brand panel — editorial statement, one accent, one line of geometry. */}
        <div className="hidden lg:flex flex-col justify-between bg-nav text-white p-12 relative overflow-hidden">
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

          <Logo variant="dark" />

          <div className="relative max-w-2xl">
            <h1
              className={`${fraunces.className} text-[64px] xl:text-[80px] leading-[1.04] tracking-tight`}
            >
              Treasury<span className="text-action">.</span>
              <br />
              Risk<span className="text-action">.</span>
              <br />
              Reporting<span className="text-action">.</span>
            </h1>
            <p className="mt-8 text-body-lg text-white/55 max-w-md leading-relaxed">
              One platform for liquidity, capital, FX, and balance-sheet
              management — built for African banks.
            </p>
          </div>

          <p className="relative text-caption text-white/35">
            © {new Date().getFullYear()} AequorOS, Inc. · Treasury and ALM
            infrastructure
          </p>
        </div>

        {/* Sign-in panel — calm, minimal, one action. */}
        <div className="flex items-center justify-center p-8 bg-surface-raised">
          <div className="w-full max-w-sm">
            <div className="lg:hidden mb-10">
              <Logo variant="light" />
            </div>

            <h2 className="text-h1 text-navy">Sign in</h2>
            <p className="mt-2 text-body text-slate leading-relaxed">
              {withSso
                ? 'Use your AequorOS credentials, or single sign-on through your institution.'
                : 'Use your AequorOS credentials.'}
            </p>

            {/* LoginForm reads ?callbackUrl via useSearchParams, which requires a
                Suspense boundary. */}
            <Suspense fallback={<div className="mt-8 h-64" aria-busy="true" />}>
              <LoginForm ssoEnabled={withSso} />
            </Suspense>
          </div>
        </div>
      </div>
    </>
  );
}
