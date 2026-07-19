import { Suspense } from 'react';
import { Shield } from 'lucide-react';
import Logo from '@/components/shell/Logo';
import PrototypeBanner from '@/components/shell/PrototypeBanner';
import LoginForm from './LoginForm';

export default function LoginPage() {
  return (
    <>
    <PrototypeBanner />
    <div className="min-h-screen grid lg:grid-cols-[1fr,minmax(0,480px)]">
      {/* Left brand panel */}
      <div className="hidden lg:flex flex-col justify-between bg-nav text-white p-12 relative overflow-hidden">
        <Logo variant="dark" />

        <div className="relative">
          <p className="text-micro font-medium uppercase tracking-wider text-white/50">
            Treasury and ALM platform
          </p>
          <h1 className="mt-4 text-[44px] leading-[1.1] font-semibold tracking-tight max-w-lg">
            Treasury reimagined for African banks.
          </h1>
          <p className="mt-6 text-body-lg text-white/70 max-w-md leading-relaxed">
            One platform for liquidity, capital, FX, and balance sheet
            management. Pre-configured for Bank of Ghana, CBN, SARB, and CBK
            reporting frameworks.
          </p>

          <dl className="mt-10 grid grid-cols-3 gap-6 max-w-lg">
            <div>
              <dt className="text-micro font-medium uppercase tracking-wider text-white/50">
                Modules
              </dt>
              <dd className="mt-1 font-mono text-h1 text-white tabular-nums">
                06
              </dd>
            </div>
            <div>
              <dt className="text-micro font-medium uppercase tracking-wider text-white/50">
                Regulators
              </dt>
              <dd className="mt-1 font-mono text-h1 text-white tabular-nums">
                04
              </dd>
            </div>
            <div>
              <dt className="text-micro font-medium uppercase tracking-wider text-white/50">
                Models
              </dt>
              <dd className="mt-1 font-mono text-h1 text-white tabular-nums">
                LSTM · DRL
              </dd>
            </div>
          </dl>
        </div>

        <p className="text-caption text-white/40">
          © {new Date().getFullYear()} AequorOS, Inc. · Demo environment ·
          Synthetic data
        </p>
      </div>

      {/* Right login panel */}
      <div className="flex items-center justify-center p-8 bg-surface-raised">
        <div className="w-full max-w-sm">
          <div className="lg:hidden mb-10">
            <Logo variant="light" />
          </div>

          <p className="text-micro font-medium uppercase tracking-wider text-action">
            Secure sign-in
          </p>
          <h2 className="mt-3 text-h1 text-navy">Sign in to AequorOS</h2>
          <p className="mt-2 text-body text-slate leading-relaxed">
            Use your AequorOS credentials, or single sign-on if your institution
            has it configured.
          </p>

          {/* LoginForm reads ?callbackUrl via useSearchParams, which requires a
              Suspense boundary so /login can be prerendered at build time. */}
          <Suspense fallback={<div className="mt-8 h-64" aria-busy="true" />}>
            <LoginForm />
          </Suspense>

          <div className="mt-8 pt-6 border-t border-border-light flex items-start gap-3 text-caption text-slate">
            <Shield size={16} className="text-success shrink-0 mt-0.5" aria-hidden />
            <p className="leading-relaxed">
              SOC 2 roadmap in progress · End-to-end encryption · BoG data
              residency · Role-based access control
            </p>
          </div>
        </div>
      </div>
    </div>
    </>
  );
}
