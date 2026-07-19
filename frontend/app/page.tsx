import SectionLabel from '@/components/SectionLabel';
import StatCard from '@/components/StatCard';
import { LinkButton } from '@/components/Button';

export default function HomePage() {
  return (
    <>
      {/* 1.1 Hero */}
      <section className="bg-navy-deep text-white min-h-[85vh] flex items-center">
        <div className="max-w-7xl mx-auto w-full px-6 md:px-12 lg:px-16 py-20">
          <div className="max-w-[1200px]">
            <SectionLabel>BUILT FOR AFRICA · PILOTING IN GHANA</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-white text-5xl md:text-6xl lg:text-7xl leading-[1.05] tracking-tight max-w-4xl">
              Treasury and ALM infrastructure for African banks.
            </h1>
            <p className="mt-8 text-ice-blue text-lg md:text-xl leading-relaxed max-w-[620px]">
              AequorOS is a cloud-native platform that automates balance sheet
              management, regulatory capital and liquidity reporting, and risk
              modeling for mid-tier banks across Africa — connected directly to
              the core banking systems they already run.
            </p>
            <div className="mt-10 flex flex-col sm:flex-row gap-4">
              <LinkButton href="/contact" variant="primary-on-dark">
                Request a pilot
              </LinkButton>
              <LinkButton href="/product" variant="secondary">
                See the platform
              </LinkButton>
            </div>
            <p className="mt-8 text-sm text-ice-blue/70">
              MVP live · connects to Oracle/FLEXCUBE, Snowflake, Temenos T24, a
              direct API, or a file upload · liquidity, capital, and regulatory
              returns generated end to end.
            </p>
          </div>
        </div>
      </section>

      {/* 1.2 The Problem */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-start">
            <div>
              <SectionLabel>THE GAP WE ADDRESS</SectionLabel>
              <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
                African banks manage billions in assets using spreadsheets.
              </h2>
              <div className="mt-8 space-y-6 text-text-primary text-base md:text-lg leading-relaxed">
                <p>
                  Mid-tier banks across Ghana, Nigeria, Kenya, and South Africa
                  run Asset-Liability Management on manual Excel workbooks and
                  quarterly Big 4 consulting engagements. Global vendors like
                  MORS, SS&amp;C Algorithmics, and Finastra price at $50,000 to
                  $200,000 per year and take six to eighteen months to
                  implement. For the 200+ banks that sit between global Tier 1
                  institutions and village cooperatives, these solutions are
                  out of reach.
                </p>
                <p>
                  Meanwhile, Basel III compliance is tightening, local
                  currencies are volatile, and central banks are demanding more
                  sophisticated stress testing, ILAAP submissions, and monthly
                  prudential reporting. The gap between what regulators expect
                  and what banks can deliver is widening.
                </p>
              </div>
            </div>
            <div className="space-y-4">
              <StatCard
                number="$200-400K"
                label="Annual Big 4 consulting spend per bank on stress testing and Basel compliance"
              />
              <StatCard
                number="10 days"
                label="Deadline for monthly prudential submissions to the Bank of Ghana, our pilot regulator"
              />
              <StatCard
                number="$50-200K+"
                label="Annual cost of global ALM vendors, unaffordable for mid-tier banks"
              />
            </div>
          </div>
        </div>
      </section>

      {/* 1.3 What's live now — proof */}
      <section className="bg-navy text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHAT&apos;S LIVE TODAY</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              A working platform, running the full pipeline today.
            </h2>
            <p className="mt-5 text-ice-blue text-lg leading-relaxed">
              The MVP runs end to end: connect to a bank&apos;s own data
              sources, normalize messy real-world data into an auditable
              canonical model, calculate, and report — automatically, on every
              accepted data load.
            </p>
          </div>

          <div className="mt-12 grid gap-px bg-white/10 rounded-lg overflow-hidden md:grid-cols-2">
            {[
              {
                title: 'One engine, many sources',
                body: 'The Data Engine connects to the systems a bank already runs — Oracle/FLEXCUBE, Snowflake, Temenos T24, a direct API, or a simple file upload — normalizes and de-duplicates the data, and lands it in an auditable canonical model. Each institution’s data is mapped to that model, so unusual sources are configured, not re-engineered.',
              },
              {
                title: 'Calculations that recompute on every load',
                body: 'Accepted data automatically triggers liquidity, capital, interest-rate, FX, FTP, and balance-sheet calculations — deterministic and regulator-defensible, with every figure traceable back to the source input that produced it.',
              },
              {
                title: 'Regulatory returns, generated',
                body: 'Bank of Ghana BSD prudential returns are produced from the platform in Bank of Ghana return formats and exported to Excel, CSV, or PDF — no re-keying, no parallel spreadsheet. Ghana is our pilot; other African regulators run on the same engine.',
              },
              {
                title: 'Auditable by construction',
                body: 'Immutable snapshots, full data lineage, and value-based reproducibility, so a past submission can be reproduced exactly — the thing a bank examiner actually asks for.',
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
              See how it works
            </LinkButton>
          </div>
        </div>
      </section>

      {/* 1.4 The Solution */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHY AEQUOROS</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Cloud-native ALM, purpose-built for Africa.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              One platform for the core Treasury and Risk workflows, on a single
              auditable data spine. Machine learning where it measurably
              improves forecasting; deterministic and auditable everywhere a
              regulator needs it to be.
            </p>
          </div>

          <div className="mt-12 grid md:grid-cols-3 gap-8">
            {[
              {
                num: '01',
                title: 'Affordable',
                body: 'SaaS pricing roughly 90% below global vendors. Built to be economically accessible for the mid-tier banks that dominate African financial markets.',
              },
              {
                num: '02',
                title: 'Rapidly deployed',
                body: 'Weeks, not the six-to-eighteen months legacy vendors take. Bank of Ghana return templates are built today; Nigeria (CBN) and South Africa (SARB) follow on the same engine.',
              },
              {
                num: '03',
                title: 'Built for this market',
                body: 'Direct integration with the cores African banks actually run — Oracle/FLEXCUBE, Temenos T24, Snowflake, or any SQL database (Finacle on the roadmap). Behavioral models tuned per institution. Regulatory reporting in each central bank’s formats.',
              },
            ].map((col) => (
              <div
                key={col.num}
                className="bg-white border border-border-light rounded-lg p-8 border-t-[3px] border-t-accent"
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

      {/* 1.5 Why Now */}
      <section className="bg-white">
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
                body: 'Central banks across the continent now expect ILAAP with stress testing, monthly capital calculations, and LCR/NSFR reporting — the Bank of Ghana among the first. Mid-tier banks are being asked for the same rigor as Tier 1 institutions, but with Excel as their primary tool.',
              },
              {
                num: '02',
                title: 'Macroeconomic stress',
                body: 'Persistent currency depreciation across the Ghanaian cedi, Nigerian naira, and other regional currencies. Inflation spikes of 20% or more. Rising sovereign yields. Banks need real-time risk management capability, not quarterly consulting reports.',
              },
              {
                num: '03',
                title: 'AI maturity',
                body: 'Machine-learning approaches now measurably outperform static methods in cash-flow forecasting and behavioral modeling, and cloud infrastructure has made enterprise-grade modeling deployable at SaaS prices for the first time. AequorOS applies ML where it earns its place, and keeps the regulatory calculations deterministic.',
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

      {/* 1.6 Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <SectionLabel>PILOT PROGRAM</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              We&apos;re onboarding a first cohort of pilot banks.
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              If you&apos;re a Treasury or Risk leader at a mid-tier African
              bank — or an advisor who wants to help build this — we want to work
              with a small number of design-partner banks. We respond to every
              serious inquiry.
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
