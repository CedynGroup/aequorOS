import SectionLabel from '@/components/SectionLabel';
import StatCard from '@/components/StatCard';
import ProductFrame from '@/components/ProductFrame';
import FeatureScreenGrid from '@/components/FeatureScreenGrid';
import { LinkButton } from '@/components/Button';
import {
  heroScreen,
  homepageFeatureScreens,
} from '@/lib/product-screens';

export default function HomePage() {
  return (
    <>
      {/* Hero — product-first */}
      <section className="bg-navy-deep text-white overflow-hidden">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pt-16 md:pt-20 lg:pt-24 pb-12 md:pb-16">
          <div className="max-w-3xl">
            <SectionLabel>BUILT FOR AFRICA · PILOTING IN GHANA</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-white text-4xl md:text-5xl lg:text-6xl leading-[1.05] tracking-tight">
              Treasury and ALM infrastructure for African banks.
            </h1>
            <p className="mt-6 text-ice-blue text-lg md:text-xl leading-relaxed max-w-[620px]">
              Cloud-native balance sheet management, regulatory capital and
              liquidity reporting, and risk modeling — connected to the cores
              mid-tier banks already run.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row gap-4">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a demo
              </LinkButton>
              <LinkButton href="/product#product-ui" variant="secondary">
                See the product
              </LinkButton>
            </div>
            <p className="mt-6 text-sm text-ice-blue/70">
              Live platform · Data Engine, six ALM engines, and BoG regulatory
              returns · onboarding pilot banks
            </p>
          </div>

          <div className="mt-12 md:mt-14 max-w-5xl">
            <ProductFrame
              screen={heroScreen}
              priority
              tone="dark"
              sizes="(max-width: 1024px) 100vw, 1024px"
            />
            <p className="mt-4 text-sm text-ice-blue/60">
              {heroScreen.title} — working product UI on a synthetic mid-tier
              bank profile (Ghana pilot).
            </p>
          </div>
        </div>
      </section>

      {/* Problem */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-start">
            <div>
              <SectionLabel>THE GAP WE ADDRESS</SectionLabel>
              <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
                African banks manage billions in assets using spreadsheets.
              </h2>
              <div className="mt-8 space-y-5 text-text-primary text-base md:text-lg leading-relaxed">
                <p>
                  Mid-tier banks across Ghana, Nigeria, Kenya, and South Africa
                  still run Asset-Liability Management on Excel workbooks and
                  quarterly Big 4 engagements. Global ALM vendors price at
                  $50–200K+ per year and take six to eighteen months to
                  implement — out of reach for the banks that sit between Tier 1
                  institutions and village cooperatives.
                </p>
                <p>
                  Meanwhile Basel III is tightening, local currencies are
                  volatile, and central banks demand real stress testing and
                  monthly prudential reporting. The gap between what regulators
                  expect and what banks can deliver is widening.
                </p>
              </div>
            </div>
            <div className="space-y-4">
              <StatCard
                number="$200–400K"
                label="Annual Big 4 consulting spend per bank on stress testing and Basel compliance"
              />
              <StatCard
                number="10 days"
                label="Deadline for monthly prudential submissions to the Bank of Ghana, our pilot regulator"
              />
              <StatCard
                number="$50–200K+"
                label="Annual cost of global ALM vendors — priced for Tier 1, not mid-tier budgets"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Product proof — public UI, no login */}
      <section className="bg-soft-bg" id="product-proof">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>THE PRODUCT</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              A launched platform — not a waitlist.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              From core data to regulatory return on one governed path. Every
              screen below is the working product interface.
            </p>
          </div>
          <div className="mt-12">
            <FeatureScreenGrid screens={homepageFeatureScreens} />
          </div>
        </div>
      </section>

      {/* What's live */}
      <section className="bg-navy text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHAT&apos;S LIVE TODAY</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Connect. Calculate. Report. Automatically.
            </h2>
          </div>

          <div className="mt-12 grid gap-px bg-white/10 rounded-lg overflow-hidden md:grid-cols-2">
            {[
              {
                title: 'Data Engine',
                body: 'Pull from Oracle/FLEXCUBE, Snowflake, Temenos T24, a secure API, or file upload. Normalize into an auditable canonical model — configured per bank, not hard-coded into the product.',
              },
              {
                title: 'Six ALM engines',
                body: 'Liquidity, capital, interest-rate risk, FX, FTP, and balance-sheet forecasting recompute on every accepted load — deterministic where regulators need them to be.',
              },
              {
                title: 'Regulatory returns',
                body: 'Bank of Ghana BSD prudential returns generated in regulator formats and exported to Excel, CSV, or PDF. Ghana is the pilot; other African regulators share the same engine.',
              },
              {
                title: 'Audit by construction',
                body: 'Immutable snapshots, full lineage, and value-based reproducibility so a past submission can be reproduced exactly — examiner-ready evidence, not a reconstructed spreadsheet.',
              },
            ].map((item) => (
              <div key={item.title} className="bg-navy p-8">
                <h3 className="font-serif font-bold text-white text-xl leading-snug">
                  {item.title}
                </h3>
                <p className="mt-3 text-ice-blue/90 leading-relaxed">
                  {item.body}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-10">
            <LinkButton href="/product" variant="primary-on-dark">
              Explore the platform
            </LinkButton>
          </div>
        </div>
      </section>

      {/* Why AequorOS */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHY AEQUOROS</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Built for mid-tier African banks — not adapted from Tier 1.
            </h2>
          </div>

          <div className="mt-12 grid md:grid-cols-3 gap-8">
            {[
              {
                num: '01',
                title: 'Priced for this market',
                body: 'SaaS economics for mid-tier budgets — a fraction of global ALM licenses that were never designed for this segment.',
              },
              {
                num: '02',
                title: 'Deployed in weeks',
                body: 'Not six-to-eighteen-month programs. BoG return templates ship today; Nigeria (CBN) and South Africa (SARB) follow on the same engine.',
              },
              {
                num: '03',
                title: 'Built for local reality',
                body: 'Cores African banks actually run, behavioral models tuned per institution, and reporting in each central bank’s formats — not a European template with a currency flag.',
              },
            ].map((col) => (
              <div
                key={col.num}
                className="bg-soft-bg border border-border-light rounded-lg p-8 border-t-[3px] border-t-accent"
              >
                <p className="font-serif text-accent text-6xl leading-none">
                  {col.num}
                </p>
                <h3 className="mt-6 font-serif font-bold text-navy text-2xl">
                  {col.title}
                </h3>
                <p className="mt-4 text-text-primary leading-relaxed">
                  {col.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Why now */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHY NOW</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Three forces converging.
            </h2>
          </div>

          <div className="mt-12 space-y-10">
            {[
              {
                num: '01',
                title: 'Regulatory tightening',
                body: 'Central banks now expect ILAAP with stress testing, monthly capital calculations, and LCR/NSFR — with mid-tier banks held to Tier 1 rigor while still on Excel.',
              },
              {
                num: '02',
                title: 'Macroeconomic stress',
                body: 'Persistent currency depreciation, inflation spikes, and rising sovereign yields mean banks need continuous risk management — not a quarterly consulting snapshot.',
              },
              {
                num: '03',
                title: 'Infrastructure finally fits',
                body: 'Cloud and modern data stacks make enterprise-grade ALM deployable at SaaS prices. AequorOS keeps regulatory calculations deterministic and examiner-defensible.',
              },
            ].map((row) => (
              <div
                key={row.num}
                className="grid md:grid-cols-[auto,1fr] gap-6 md:gap-10 items-start border-l-4 border-accent pl-6 md:pl-8"
              >
                <div className="font-serif font-bold text-accent text-6xl leading-none w-24">
                  {row.num}
                </div>
                <div>
                  <h3 className="font-serif font-bold text-navy text-2xl">
                    {row.title}
                  </h3>
                  <p className="mt-3 text-text-primary text-base md:text-lg leading-relaxed max-w-3xl">
                    {row.body}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <SectionLabel>TALK TO US</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Ready for a walkthrough on a bank like yours?
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              Thirty minutes with a Treasury or Risk leader: Data Engine, live
              calculations, and regulatory returns. We&apos;re onboarding a first
              cohort of design-partner banks and respond to every serious
              inquiry.
            </p>
            <div className="mt-10 flex flex-col sm:flex-row gap-4 justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a demo
              </LinkButton>
              <LinkButton href="/product#product-ui" variant="secondary">
                Browse the product UI
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
