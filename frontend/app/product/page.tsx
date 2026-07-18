import type { Metadata } from 'next';
import { Check } from 'lucide-react';
import SectionLabel from '@/components/SectionLabel';
import ModuleCard from '@/components/ModuleCard';
import { LinkButton } from '@/components/Button';

export const metadata: Metadata = {
  title: 'Product — AequorOS',
  description:
    'A working Treasury and ALM platform for African banks: a source-agnostic Data Engine connected to the core, automated liquidity, capital, and balance-sheet calculations, and Bank of Ghana regulatory reporting — auditable end to end.',
};

const modules = [
  {
    number: '01',
    name: 'Liquidity Risk',
    description:
      'LCR, NSFR, and cash-flow forecasting at the portfolio and institution level, recalculated automatically as new data lands.',
    ai: 'LSTM-based cash-flow forecasting and per-institution behavioral models for non-maturity deposits and prepayment.',
  },
  {
    number: '02',
    name: 'Regulatory Capital',
    description:
      'RWA calculations under Basel III standardized approach, capital pressure indicators, and pre-built Bank of Ghana BSD prudential returns.',
    ai: 'Automated validation against regulatory thresholds and submission-ready report generation.',
  },
  {
    number: '03',
    name: 'Balance Sheet Forecasting',
    description:
      'Multi-year balance-sheet projection and scenario planning against macro assumptions, run as immutable, reproducible snapshots.',
    ai: 'ML-assisted forecasting on 10-year training data; strategic capital-allocation optimization on the roadmap.',
  },
  {
    number: '04',
    name: 'Interest Rate Risk',
    description:
      'Gap analysis, duration analysis, and economic value of equity (EVE) for interest-rate exposure — deterministic and fully auditable.',
    ai: 'Hedging optimization under volatile rate environments is on the roadmap.',
  },
  {
    number: '05',
    name: 'Funds Transfer Pricing',
    description:
      'Transfer-pricing curves, non-maturity-deposit behavioral modeling, and product-level profitability analysis.',
    ai: 'Behavioral-model calibration from historical transaction data, per institution.',
  },
  {
    number: '06',
    name: 'FX Risk',
    description:
      'Currency-exposure measurement and hedging analysis for emerging-market currency pairs (cedi, naira, and regional).',
    ai: 'Machine-learning currency models for regional pairs are on the roadmap.',
  },
];

const steps = [
  {
    n: '1',
    title: 'Connect',
    body: 'A source-agnostic Data Engine connects directly to your core over a secure, read-only link — proven live against an Oracle/FLEXCUBE core. Temenos T24 and Finacle are on the roadmap, and file upload or API push work today for any source.',
  },
  {
    n: '2',
    title: 'Calculate',
    body: 'The engine normalizes real-world data into an auditable canonical model, then runs liquidity, capital, and balance-sheet calculations automatically. Deterministic and regulator-defensible where it must be; machine learning where it measurably improves forecasting.',
  },
  {
    n: '3',
    title: 'Report',
    body: 'ALCO reports, calculation outputs, and Bank of Ghana BSD returns are generated in the formats your bank and your central bank require — every figure traceable back to the source input that produced it.',
  },
];

const infrastructure = [
  'Cloud-native; Python/FastAPI backend, TypeScript front end',
  'PostgreSQL canonical store with row-level tenant isolation',
  'Per-institution object storage (MinIO for MVP → GCS/S3 for production)',
  'Immutable, versioned snapshots with full data lineage',
  'SOC 2 readiness on the roadmap ahead of production banking data',
];

const security = [
  'Encryption at rest and in transit',
  'Role-based access control (RBAC)',
  'Full audit trail and data lineage on every record',
  'Value-based reproducibility for point-in-time regulatory submissions',
  'Per-institution data residency options',
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
              From your core to your regulator, in one platform.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[720px]">
              AequorOS covers the Treasury and Risk workflows a modern bank runs
              on — ingestion, ALM calculation, and regulatory reporting — built
              on a single auditable data spine. Banks adopt the full platform or
              start with the workflows most critical to their operations.
            </p>
          </div>
        </div>
      </section>

      {/* 2.2 The Data Engine — the differentiator */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-start">
            <div>
              <SectionLabel>THE DATA ENGINE</SectionLabel>
              <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
                The hard part, already working.
              </h2>
              <div className="mt-6 space-y-5 text-ice-blue text-base md:text-lg leading-relaxed">
                <p>
                  Most of the work in bank ALM is getting messy core-banking data
                  into a clean, trustworthy shape. AequorOS does this with a
                  source-agnostic Data Engine that pulls from the core,
                  normalizes it, resolves duplicates, and lands it in an
                  auditable canonical model — automatically triggering the
                  downstream calculations.
                </p>
                <p>
                  It is proven, not theoretical: a single validated run pulled
                  roughly 167,000 records from a live Oracle/FLEXCUBE core over a
                  secure connection and carried them all the way through to
                  canonical positions and calculated metrics.
                </p>
              </div>
            </div>
            <div className="rounded-lg bg-white/5 border border-white/10 p-8">
              <ul className="space-y-5">
                {[
                  'Direct core-banking pull (Oracle/FLEXCUBE proven; T24 & Finacle on the roadmap)',
                  'File upload and API push for any source, today',
                  'Normalization, deduplication, and reference resolution',
                  'Immutable canonical model with end-to-end lineage',
                  'Auto-calculation on every accepted data load',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <Check size={20} className="text-accent shrink-0 mt-0.5" aria-hidden />
                    <span className="text-white leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* 2.3 The Modules */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>THE WORKFLOWS</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              The Treasury and Risk workflows, on one spine.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              Liquidity, capital, and balance-sheet forecasting run today on the
              live pipeline; interest-rate risk, FTP, and FX round out the
              platform. Everything shares the same auditable canonical data.
            </p>
          </div>
          <div className="mt-12 grid gap-6 md:gap-8 md:grid-cols-2">
            {modules.map((m) => (
              <ModuleCard key={m.number} {...m} />
            ))}
          </div>
        </div>
      </section>

      {/* 2.4 How It Works */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>HOW IT WORKS</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              How AequorOS fits into a bank&apos;s operations.
            </h2>
          </div>

          <div className="mt-12 grid gap-8 md:grid-cols-3">
            {steps.map((s) => (
              <div key={s.n} className="border-l-4 border-accent pl-6 py-2">
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

      {/* 2.5 Technical Foundation */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>TECHNICAL FOUNDATION</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Built to survive a bank IT review.
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
                    <Check size={20} className="text-accent shrink-0 mt-0.5" aria-hidden />
                    <span className="text-text-primary leading-relaxed">{item}</span>
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
                    <Check size={20} className="text-accent shrink-0 mt-0.5" aria-hidden />
                    <span className="text-text-primary leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* 2.6 See it live */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-16 md:pb-20 lg:pb-24">
          <div className="border border-border-light border-l-4 border-l-accent rounded-lg p-8 md:p-10 grid lg:grid-cols-[2fr,1fr] gap-8 items-center">
            <div>
              <SectionLabel>SEE IT LIVE</SectionLabel>
              <h2 className="mt-4 font-serif font-bold text-navy text-2xl md:text-3xl leading-tight">
                Walk through the working platform.
              </h2>
              <p className="mt-4 text-text-primary text-base md:text-lg leading-relaxed max-w-2xl">
                A guided demo of the data engine, ALM calculations, and Bank of
                Ghana reporting, running on a Bank of Ghana–licensed mid-tier
                universal bank profile with synthetic data. Built for Treasury
                and Risk teams evaluating workflow, reporting, and auditability.
              </p>
            </div>
            <div className="flex lg:justify-end">
              <LinkButton href="https://demo.aequoros.com" variant="primary" external>
                Open the demo
              </LinkButton>
            </div>
          </div>
        </div>
      </section>

      {/* 2.7 Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Ready to run it on your bank&apos;s data?
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              We&apos;re onboarding a small first cohort of pilot banks. If
              you&apos;re a Treasury or Risk leader, we&apos;d like to work with
              you.
            </p>
            <div className="mt-10 flex justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a pilot
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
