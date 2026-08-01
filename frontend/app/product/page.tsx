import type { Metadata } from 'next';
import { Check } from 'lucide-react';
import SectionLabel from '@/components/SectionLabel';
import ModuleCard from '@/components/ModuleCard';
import ProductFrame from '@/components/ProductFrame';
import ProductGallery from '@/components/ProductGallery';
import { LinkButton } from '@/components/Button';
import { heroScreen, screenById } from '@/lib/product-screens';

export const metadata: Metadata = {
  title: 'Product — AequorOS',
  description:
    'Live Treasury and ALM platform for African banks: Data Engine, liquidity, capital, IRRBB, FX, FTP, forecasting, and Bank of Ghana regulatory returns — with the working product interface shown publicly.',
};

const modules = [
  {
    number: '01',
    name: 'Liquidity Risk',
    description:
      'LCR, NSFR, and cash-flow forecasting at portfolio and institution level, recalculated automatically as new data lands.',
    detail:
      'Shock-scenario liquidity stress on the same engine. ML cash-flow views are labeled separately from regulatory ratios.',
    screenId: 'liquidity',
  },
  {
    number: '02',
    name: 'Regulatory Capital',
    description:
      'RWA under the Basel III standardized approach, capital stack and ratios, and pre-built Bank of Ghana BSD prudential returns.',
    detail:
      'CET1 / Tier 1 / CAR headroom against regulatory floors, with multi-quarter capital stress paths.',
    screenId: 'basel',
  },
  {
    number: '03',
    name: 'Balance Sheet Forecasting',
    description:
      'Multi-year projection and scenario planning against macro assumptions, run as immutable, reproducible snapshots.',
    detail:
      'What-if lab re-runs the real regulatory engines under shock — base vs stressed paths with breach flags.',
    screenId: 'forecasting-whatif',
  },
  {
    number: '04',
    name: 'Interest Rate Risk',
    description:
      'Repricing-gap and duration analysis, EVE, and Earnings-at-Risk across the full Basel IRRBB scenario set.',
    detail:
      'Interest-rate-swap decomposition included. Deterministic and fully auditable for examiner review.',
    screenId: 'irr',
  },
  {
    number: '05',
    name: 'Funds Transfer Pricing',
    description:
      'Matched-maturity transfer-pricing curves, NMD behavioral modeling, and product- and branch-level profitability.',
    detail:
      'Core/volatile deposit splits driven by reviewed behavioral duration — not folklore spreads.',
    screenId: 'ftp',
  },
  {
    number: '06',
    name: 'FX Risk',
    description:
      'Net-open-position monitoring against limits, historical-simulation and stressed VaR, and IFRS 9 hedge-effectiveness testing.',
    detail:
      'Regional currency pairs and BoG-style single-currency and aggregate NOP limits.',
    screenId: 'fx',
  },
];

const steps = [
  {
    n: '1',
    title: 'Connect',
    body: 'The Data Engine connects to systems you already run — Oracle/FLEXCUBE, Snowflake, Temenos T24, a secure API, or file upload. Each institution maps to the canonical model; unusual sources are configured, not hard-coded.',
  },
  {
    n: '2',
    title: 'Calculate',
    body: 'Accepted loads normalize into an auditable model and trigger liquidity, capital, IRR, FX, FTP, and forecasting. Deterministic where regulation demands it; ML only where it improves forecasting and is clearly labeled.',
  },
  {
    n: '3',
    title: 'Report',
    body: 'ALCO outputs and Bank of Ghana BSD returns export to Excel, CSV, and PDF — every figure traceable to the source input that produced it.',
  },
];

const infrastructure = [
  'Cloud-native; Python/FastAPI backend, TypeScript front end',
  'PostgreSQL canonical store with row-level tenant isolation',
  'Per-institution, cloud-based object storage',
  'Immutable, versioned snapshots with full data lineage',
  'SOC 2 readiness on the roadmap ahead of production banking data',
];

const security = [
  'Encryption in transit and at rest',
  'Role-based access control (RBAC)',
  'Immutable lineage on every canonical record; audit trail on every review and mutation',
  'Value-based reproducibility for point-in-time regulatory submissions',
  'Per-tenant isolation enforced at the database (row-level security)',
];

export default function ProductPage() {
  const dataEngine = screenById('data-engine')!;
  const submissions = screenById('submissions')!;
  const lineage = screenById('positions-lineage')!;

  return (
    <>
      {/* Hero */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20">
          <div className="max-w-4xl">
            <SectionLabel>THE PLATFORM</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-navy text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              From your core to your regulator, in one platform.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[720px]">
              Ingestion, ALM calculation, and regulatory reporting on a single
              auditable data spine. Banks adopt the full platform or start with
              the workflows most critical to their operations.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row gap-4">
              <LinkButton href="/contact" variant="primary">
                Request a demo
              </LinkButton>
              <LinkButton href="#product-ui" variant="secondary-on-light">
                Browse product UI
              </LinkButton>
            </div>
          </div>
          <div className="mt-12 max-w-5xl">
            <ProductFrame
              screen={heroScreen}
              priority
              tone="light"
              showCaption
              sizes="(max-width: 1024px) 100vw, 1024px"
            />
          </div>
        </div>
      </section>

      {/* Data Engine */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-start">
            <div>
              <SectionLabel>THE DATA ENGINE</SectionLabel>
              <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
                Connect any source. Normalize everything.
              </h2>
              <div className="mt-6 space-y-5 text-ice-blue text-base md:text-lg leading-relaxed">
                <p>
                  Most of the work in bank ALM is getting messy core-banking
                  data into a clean, trustworthy shape. AequorOS does this with
                  a source-agnostic Data Engine: connect, normalize,
                  de-duplicate, resolve references, land an auditable canonical
                  model — then trigger downstream calculations automatically.
                </p>
                <p>
                  Where a source is unusual, the mapping is configured for that
                  bank. It is never hard-coded into the product.
                </p>
              </div>
              <ul className="mt-8 space-y-4">
                {[
                  'Direct database pull from Oracle/FLEXCUBE, Snowflake, SQL Server, or generic JDBC/ODBC',
                  'Temenos T24 adapter today; Finacle on the roadmap',
                  'File upload and API push for any source',
                  'Per-institution mapping — no custom product forks',
                  'Immutable canonical model with end-to-end lineage',
                  'Automatic recalculation on every accepted data load',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <Check
                      size={20}
                      className="text-accent shrink-0 mt-0.5"
                      aria-hidden
                    />
                    <span className="text-white leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
            <ProductFrame
              screen={dataEngine}
              tone="dark"
              showCaption={false}
              sizes="(max-width: 1024px) 100vw, 560px"
            />
          </div>
        </div>
      </section>

      {/* Modules with UI */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>THE WORKFLOWS</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Six engines on one live spine.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              Liquidity, capital, forecasting, interest-rate risk, FTP, and FX
              share one auditable canonical model — each with its working
              product interface below.
            </p>
          </div>
          <div className="mt-12 grid gap-6 md:gap-8 md:grid-cols-2">
            {modules.map((m) => (
              <ModuleCard
                key={m.number}
                number={m.number}
                name={m.name}
                description={m.description}
                detail={m.detail}
                screen={screenById(m.screenId)}
              />
            ))}
          </div>
        </div>
      </section>

      {/* Submissions + lineage proof */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>REGULATORY &amp; AUDIT</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Returns you can defend. Lineage you can show.
            </h2>
          </div>
          <div className="mt-12 grid gap-10 lg:grid-cols-2">
            <ProductFrame screen={submissions} tone="light" showCaption />
            <ProductFrame screen={lineage} tone="light" showCaption />
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="bg-soft-bg">
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

      {/* Interactive product UI gallery — public proof */}
      <section
        id="product-ui"
        className="bg-white scroll-mt-20"
      >
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl mb-10">
            <SectionLabel>PRODUCT INTERFACE</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Browse the working product — no login required.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              Captures from the live platform on a synthetic mid-tier African
              universal bank profile (Ghana pilot). This is the product surface
              Treasury and Risk teams evaluate in a demo.
            </p>
          </div>
          <ProductGallery />
        </div>
      </section>

      {/* Technical foundation */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>TECHNICAL FOUNDATION</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Built for a bank IT review.
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

      {/* Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Ready to run it on your bank&apos;s questions?
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              Book a 30-minute walkthrough with a Treasury or Risk leader. We
              walk the live platform — Data Engine, calculations, and regulatory
              returns — against the workflows your bank actually runs.
            </p>
            <div className="mt-10 flex justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a demo
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
