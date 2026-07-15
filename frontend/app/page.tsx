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
            <SectionLabel>PRE-SEED · FINTECH INFRASTRUCTURE</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-white text-5xl md:text-6xl lg:text-7xl leading-[1.05] tracking-tight max-w-4xl">
              Treasury and ALM infrastructure for African banks.
            </h1>
            <p className="mt-8 text-ice-blue text-lg md:text-xl leading-relaxed max-w-[600px]">
              AequorOS is a cloud-native platform that automates balance sheet
              management, regulatory capital reporting, and risk modeling for
              mid-tier banks across sub-Saharan Africa.
            </p>
            <div className="mt-10 flex flex-col sm:flex-row gap-4">
              <LinkButton href="/contact" variant="primary-on-dark">
                Start a conversation
              </LinkButton>
              <LinkButton href="/product" variant="secondary">
                Learn more
              </LinkButton>
            </div>
            <p className="mt-8 text-sm text-ice-blue/70">
              Currently in stealth. Building. Talking to banks.
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
                label="Deadline for Bank of Ghana monthly prudential submissions"
              />
              <StatCard
                number="$50-200K+"
                label="Annual cost of global ALM vendors, unaffordable for mid-tier banks"
              />
            </div>
          </div>
        </div>
      </section>

      {/* 1.3 The Solution */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>OUR APPROACH</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Cloud-native ALM, purpose-built for Africa.
            </h2>
            <p className="mt-5 text-text-muted text-lg leading-relaxed">
              One platform. Six integrated modules. AI where it genuinely
              improves outcomes.
            </p>
          </div>

          <div className="mt-12 grid md:grid-cols-3 gap-8">
            {[
              {
                num: '01',
                title: 'Affordable',
                body: '$10-20K per month SaaS, roughly 90% cheaper than global vendors. Built to be economically accessible for the mid-tier banks that dominate African financial markets.',
              },
              {
                num: '02',
                title: 'Rapidly deployed',
                body: 'Four to eight week implementation versus six to eighteen months for legacy vendors. Pre-configured Bank of Ghana, Central Bank of Nigeria, and South African Reserve Bank regulatory templates.',
              },
              {
                num: '03',
                title: 'Built for this market',
                body: 'Machine learning models trained on emerging market data. Temenos T24 integration at the core. Regulatory reporting that speaks the language each central bank requires.',
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

      {/* 1.4 Why Now */}
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
                body: 'Bank of Ghana now mandates ILAAP with stress testing, monthly capital calculations, and LCR/NSFR reporting. Basel III Endgame is rolling out globally. Mid-tier banks are being asked for the same rigor as Tier 1 institutions, but with Excel as their primary tool.',
              },
              {
                num: '02',
                title: 'Macroeconomic stress',
                body: 'Persistent currency depreciation across the Ghanaian cedi, Nigerian naira, and other regional currencies. Inflation spikes of 20% or more. Rising sovereign yields. Banks need real-time risk management capability, not quarterly consulting reports.',
              },
              {
                num: '03',
                title: 'AI maturity',
                body: 'Research from ETH Zurich, JPMorgan, and the Basel Committee shows machine learning approaches outperforming traditional static methods in cash flow forecasting and hedging by 30 to 40 percent. Cloud infrastructure has made enterprise AI deployable at SaaS prices for the first time.',
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

      {/* 1.5 Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <SectionLabel>JOIN US</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              We&apos;re building the infrastructure African banks deserve.
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              If you&apos;re a bank executive, potential advisor, investor, or
              engineer who wants to help build this, we&apos;d like to hear from
              you.
            </p>
            <div className="mt-10 flex justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Get in touch
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
