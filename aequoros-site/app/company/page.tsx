import type { Metadata } from 'next';
import SectionLabel from '@/components/SectionLabel';
import TeamMember from '@/components/TeamMember';
import { LinkButton } from '@/components/Button';
import { team } from '@/lib/team';

export const metadata: Metadata = {
  title: 'Company — AequorOS',
  description:
    'AequorOS is a pre-seed fintech building Treasury and ALM infrastructure for African banks. Founded in 2025, headquartered virtually between Winchester, VA and Accra, Ghana.',
};

const statusCards = [
  {
    title: 'Market Validation',
    status: 'IN PROGRESS',
    body: 'Running structured research interviews with Ghana banks across all three tiers. Engaging with Bank of Ghana to understand certification pathways.',
  },
  {
    title: 'Product Specification',
    status: 'COMPLETE',
    body: 'Full technical specification across six modules. Regulatory reporting templates drafted for Bank of Ghana, Central Bank of Nigeria, and South African Reserve Bank.',
  },
  {
    title: 'MVP Development',
    status: 'POST-FUNDING',
    body: 'Core platform build begins upon closing seed round. Initial release covers Interest Rate Risk, Liquidity Risk, and Regulatory Capital modules.',
  },
  {
    title: 'Seed Round',
    status: 'ACTIVELY RAISING',
    body: '$1.25M seed round to validate, build MVP, and land first 5 pilot banks over 18 months.',
  },
];

export default function CompanyPage() {
  return (
    <>
      {/* 3.1 Company Hero */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-24 lg:py-28">
          <div className="max-w-4xl">
            <SectionLabel>OUR COMPANY</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-navy text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              AequorOS is building the financial infrastructure Africa
              deserves.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[720px]">
              Founded in 2025, headquartered virtually between Winchester,
              Virginia and Accra, Ghana. Currently in stealth, building, and
              talking to banks.
            </p>
          </div>
        </div>
      </section>

      {/* 3.2 Mission */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-16 md:pb-20 lg:pb-24">
          <div className="grid lg:grid-cols-[1fr,1fr] gap-12 lg:gap-16 items-start">
            <div>
              <SectionLabel>MISSION</SectionLabel>
              <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
                Why we&apos;re building this.
              </h2>
              <div className="mt-8 space-y-6 text-text-primary text-base md:text-lg leading-relaxed max-w-[800px]">
                <p>
                  Africa&apos;s banking sector manages over $2 trillion in
                  assets and serves hundreds of millions of customers. But the
                  infrastructure banks rely on to manage that capital was built
                  for a different context — large, slow-moving institutions in
                  stable currencies operating under mature regulatory
                  frameworks.
                </p>
                <p>
                  African banks need something different. Tools that are
                  affordable. Rapidly deployable. Built for volatile
                  currencies, rapidly-evolving regulations, and the
                  locally-dominant core banking systems that actually run
                  African finance.
                </p>
                <p>
                  We believe that if mid-tier African banks get access to
                  world-class risk management infrastructure at a price they
                  can afford, they&apos;ll extend more credit, serve more
                  customers, and weather macroeconomic shocks better. That,
                  ultimately, is how the continent&apos;s financial system gets
                  stronger.
                </p>
              </div>
            </div>

            <aside className="bg-navy-deep text-white rounded-lg p-8 md:p-10 border-l-4 border-accent lg:sticky lg:top-24">
              <p className="font-serif italic text-white text-xl md:text-2xl leading-relaxed">
                &ldquo;What we&apos;re building is prosaic but important: the
                computational backbone that determines whether African banks
                can safely, efficiently, and profitably serve the growing
                capital needs of their economies.&rdquo;
              </p>
              <p className="mt-6 text-ice-blue text-sm">
                Eric Inkoom Danso, Founder
              </p>
            </aside>
          </div>
        </div>
      </section>

      {/* 3.3 Team */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <SectionLabel>{team.length > 1 ? 'TEAM' : 'FOUNDER'}</SectionLabel>
          <div className="mt-8 space-y-16 lg:space-y-24">
            {team.map((member) => (
              <TeamMember key={member.name} member={member} />
            ))}
          </div>
        </div>
      </section>

      {/* 3.4 Status */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHERE WE ARE</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Currently in stealth. Building. Talking to banks.
            </h2>
          </div>

          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {statusCards.map((card) => (
              <article
                key={card.title}
                className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-7"
              >
                <div className="flex items-baseline justify-between gap-4">
                  <h3 className="font-serif font-bold text-navy text-xl">
                    {card.title}
                  </h3>
                  <span className="text-accent text-xs font-semibold tracking-[0.15em]">
                    {card.status}
                  </span>
                </div>
                <p className="mt-4 text-text-primary leading-relaxed">
                  {card.body}
                </p>
              </article>
            ))}
          </div>

          <div className="mt-10">
            <LinkButton href="/investors" variant="primary">
              View investor materials
            </LinkButton>
          </div>
        </div>
      </section>

      {/* 3.5 Closing CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Join us early.
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              We&apos;re looking for advisors, potential pilot customers,
              engineers, and investors who want to build the financial
              infrastructure African banks have been waiting for.
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
