import type { Metadata } from 'next';
import { Check } from 'lucide-react';
import SectionLabel from '@/components/SectionLabel';
import ModuleCard from '@/components/ModuleCard';
import { LinkButton } from '@/components/Button';

export const metadata: Metadata = {
  title: 'Product — AequorOS',
  description:
    'Six integrated modules for Treasury and Risk teams at African banks: Interest Rate Risk, Liquidity Risk, FX Risk, Regulatory Capital, Funds Transfer Pricing, and Balance Sheet Forecasting.',
};

const modules = [
  {
    number: '01',
    name: 'Interest Rate Risk',
    description:
      'Gap analysis, duration analysis, and economic value of equity calculations for interest rate exposure.',
    ai: 'Deep reinforcement learning for hedging optimization under volatile rate environments.',
  },
  {
    number: '02',
    name: 'Liquidity Risk',
    description:
      'LCR, NSFR, and cash flow forecasting at the portfolio and institution level.',
    ai: 'LSTM neural networks for cash flow prediction, reducing forecasting error by 30-40% versus traditional methods.',
  },
  {
    number: '03',
    name: 'FX Risk',
    description:
      'Currency exposure measurement and optimal hedging strategy for emerging market currency pairs.',
    ai: 'Ensemble XGBoost and LSTM models for cedi, naira, and regional currency prediction.',
  },
  {
    number: '04',
    name: 'Regulatory Capital',
    description:
      'Automated RWA calculations under Basel III standardized and internal models approaches. Pre-built BoG, CBN, and SARB reporting.',
    ai: 'Automated validation against regulatory thresholds and submission-ready report generation.',
  },
  {
    number: '05',
    name: 'Funds Transfer Pricing',
    description:
      'Dynamic transfer pricing curves, non-maturity deposit behavioral modeling, and product-level profitability analysis.',
    ai: 'Behavioral model calibration using historical transaction data.',
  },
  {
    number: '06',
    name: 'Balance Sheet Forecasting',
    description:
      'Strategic scenario planning, multi-year balance sheet projection, and capital allocation optimization.',
    ai: 'Reinforcement learning for strategic balance sheet optimization under macro scenarios.',
  },
];

const steps = [
  {
    n: '1',
    title: 'Connect',
    body: "AequorOS integrates with your core banking system. Pre-built connectors for Temenos T24, with support for Finacle and FlexCube. Data flows via API or batch, whichever your infrastructure supports.",
  },
  {
    n: '2',
    title: 'Calculate',
    body: 'The platform runs continuous ALM calculations, stress tests, and regulatory models against live data. Machine learning models handle forecasting and optimization. Traditional calculations handle what needs to be auditable and regulator-ready.',
  },
  {
    n: '3',
    title: 'Report',
    body: 'ALCO reports, stress test results, regulatory submissions, and executive dashboards are generated in the formats your bank and your central bank require. Submit BoG BSD returns, CBN returns, or SARB reports directly from the platform.',
  },
];

const infrastructure = [
  'Cloud-native on AWS',
  'PostgreSQL for transactional data',
  'Snowflake for analytical workloads',
  'Python and TypeScript throughout',
  'SOC 2 compliance roadmap in progress',
];

const security = [
  'End-to-end encryption',
  'Role-based access control (RBAC)',
  'Full audit trail and data lineage',
  'Model risk management aligned with SR 11-7',
  'Data residency options for each jurisdiction',
];

export default function ProductPage() {
  return (
    <>
      {/* 2.1 Product Hero */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-24 lg:py-28">
          <div className="max-w-4xl">
            <SectionLabel>THE PLATFORM</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-navy text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              Six integrated modules. One platform.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[700px]">
              AequorOS covers the core workflows that Treasury and Risk teams
              need to run a modern bank. Banks adopt the full platform or start
              with the modules most critical to their operations.
            </p>
          </div>
        </div>
      </section>

      {/* 2.2 The Six Modules */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="grid gap-6 md:gap-8 md:grid-cols-2">
            {modules.map((m) => (
              <ModuleCard key={m.number} {...m} />
            ))}
          </div>
        </div>
      </section>

      {/* 2.3 How It Works */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>UNDER THE HOOD</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              How AequorOS fits into a bank&apos;s operations.
            </h2>
          </div>

          <div className="mt-12 grid gap-8 md:grid-cols-3">
            {steps.map((s) => (
              <div
                key={s.n}
                className="border-l-4 border-accent pl-6 py-2"
              >
                <div className="w-12 h-12 rounded-md bg-navy-deep text-white font-serif font-bold text-xl flex items-center justify-center">
                  {s.n}
                </div>
                <h3 className="mt-5 font-serif font-bold text-navy text-2xl">
                  {s.title}
                </h3>
                <p className="mt-3 text-text-primary leading-relaxed">
                  {s.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 2.4 Technical Foundation */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>TECHNICAL FOUNDATION</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Built on modern cloud infrastructure.
            </h2>
          </div>

          <div className="mt-12 grid gap-10 md:grid-cols-2">
            <div className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-8">
              <h3 className="font-serif font-bold text-navy text-2xl">
                Infrastructure
              </h3>
              <ul className="mt-6 space-y-4">
                {infrastructure.map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <Check
                      size={20}
                      className="text-accent shrink-0 mt-0.5"
                      aria-hidden
                    />
                    <span className="text-text-primary leading-relaxed">
                      {item}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-8">
              <h3 className="font-serif font-bold text-navy text-2xl">
                Security and governance
              </h3>
              <ul className="mt-6 space-y-4">
                {security.map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <Check
                      size={20}
                      className="text-accent shrink-0 mt-0.5"
                      aria-hidden
                    />
                    <span className="text-text-primary leading-relaxed">
                      {item}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* 2.4b Interactive prototype */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-16 md:pb-20 lg:pb-24">
          <div className="border border-border-light border-l-4 border-l-accent rounded-lg p-8 md:p-10 grid lg:grid-cols-[2fr,1fr] gap-8 items-center">
            <div>
              <SectionLabel>INTERACTIVE PROTOTYPE</SectionLabel>
              <h2 className="mt-4 font-serif font-bold text-navy text-2xl md:text-3xl leading-tight">
                See the platform in motion.
              </h2>
              <p className="mt-4 text-text-primary text-base md:text-lg leading-relaxed max-w-2xl">
                A click-through prototype of all six modules built with synthetic
                data for a Bank of Ghana–licensed mid-tier universal bank. Useful
                for investors evaluating execution and for Treasury teams curious
                about workflow and reporting structure. Not a live system.
              </p>
            </div>
            <div className="flex lg:justify-end">
              <LinkButton
                href="https://demo.aequoros.com"
                variant="primary"
                external
              >
                Open the prototype
              </LinkButton>
            </div>
          </div>
        </div>
      </section>

      {/* 2.5 Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Want to see more?
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              We&apos;re running a structured research program with Ghana banks
              to validate the platform&apos;s fit. If you&apos;re a Treasury or
              Risk leader at a bank, we&apos;d like to include you.
            </p>
            <div className="mt-10 flex justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a conversation
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
