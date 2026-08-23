import type { Metadata } from 'next';
import SectionLabel from '@/components/SectionLabel';
import TeamMember from '@/components/TeamMember';
import ProductFrame from '@/components/ProductFrame';
import { LinkButton } from '@/components/Button';
import { team } from '@/lib/team';
import { heroScreen } from '@/lib/product-screens';

export const metadata: Metadata = {
  title: 'Company — AequorOS',
  description:
    'AequorOS builds Treasury and ALM infrastructure for African banks. Founded in 2025 with a live product platform, working across Winchester, VA and Accra, Ghana. Meet the founder and CTO.',
};

const statusCards = [
  {
    title: 'Platform',
    status: 'LIVE',
    body: 'End-to-end product: Data Engine, six ALM engines, and regulatory reporting — the working UI is shown publicly on this site, running on a synthetic bank profile.',
  },
  {
    title: 'Data Engine',
    status: 'LIVE',
    body: 'File upload and secure API push are available today, and Oracle read-only extraction ships in the standard deployment. Other database backends and core-banking adapters are built and tested but need a driver-carrying deployment image and vendor onboarding.',
  },
  {
    title: 'Regulatory reporting',
    status: 'LIVE',
    body: 'Bank of Ghana BSD prudential returns generated in the regulator’s own workbook layouts, export-ready to Excel, CSV, and PDF for officer review and signature. CBN and SARB share the same engine on the roadmap.',
  },
  {
    title: 'Pilot banks',
    status: 'IN DISCUSSION',
    body: 'We are in conversation with a first cohort of design-partner banks. No bank is in production on the platform yet, and no return has been filed with the Bank of Ghana.',
  },
];

export default function CompanyPage() {
  return (
    <>
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-24 lg:py-28">
          <div className="max-w-4xl">
            <SectionLabel>OUR COMPANY</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-navy text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              Treasury and risk infrastructure, built for African banks.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[720px]">
              Founded in 2025, working across Winchester, Virginia and Accra,
              Ghana. The product is built and running — Data Engine through
              regulatory returns — and we&apos;re in conversation with our first
              design-partner banks.
            </p>
          </div>
        </div>
      </section>

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
                  stable currencies under mature regulatory frameworks.
                </p>
                <p>
                  African banks need something different: tools that are
                  affordable, quick to stand up, and built for volatile
                  currencies, fast-moving regulation, and the core banking
                  systems that actually run finance on the continent.
                </p>
                <p>
                  When a mid-tier African bank can manage liquidity, capital,
                  and risk with infrastructure it can afford, it can extend more
                  credit, serve more customers, and hold up better through a
                  shock. That is how the financial system gets stronger.
                </p>
              </div>
            </div>

            <aside className="bg-navy-deep text-white rounded-lg p-8 md:p-10 border-l-4 border-accent lg:sticky lg:top-24">
              <p className="font-serif italic text-white text-xl md:text-2xl leading-relaxed">
                &ldquo;African banks are being asked for Tier 1 rigor with
                spreadsheet tools. We build the computational backbone that lets
                them manage liquidity, capital, and risk safely — at a price a
                mid-tier bank can actually afford.&rdquo;
              </p>
              <p className="mt-6 text-ice-blue text-sm">
                Eric Inkoom Danso, Founder &amp; CEO
              </p>
              <a
                href="https://linkedin.com/in/eidanso"
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-block text-accent text-sm font-medium hover:underline"
              >
                linkedin.com/in/eidanso
              </a>
            </aside>
          </div>
        </div>
      </section>

      {/* Product proof on company page for Google/transparency reviewers */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20">
          <div className="max-w-3xl mb-10">
            <SectionLabel>THE PRODUCT</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              A working platform, not a pitch-deck mockup.
            </h2>
            <p className="mt-4 text-text-muted text-lg leading-relaxed">
              The product interface is public on this domain — no login required
              to evaluate what we&apos;ve built.
            </p>
          </div>
          <div className="max-w-5xl">
            <ProductFrame
              screen={heroScreen}
              tone="light"
              showCaption
              sizes="(max-width: 1024px) 100vw, 1024px"
            />
          </div>
          <div className="mt-8">
            <LinkButton href="/product#product-ui" variant="secondary-on-light">
              Browse full product UI
            </LinkButton>
          </div>
        </div>
      </section>

      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <SectionLabel>{team.length > 1 ? 'TEAM' : 'FOUNDER'}</SectionLabel>
          <p className="mt-4 text-text-muted max-w-2xl leading-relaxed">
            Core team, verifiable on LinkedIn. We build in public enough for a
            bank or partner to know who they are talking to.
          </p>
          <div className="mt-10 space-y-16 lg:space-y-24">
            {team.map((member) => (
              <TeamMember key={member.name} member={member} />
            ))}
          </div>
        </div>
      </section>

      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div className="max-w-3xl">
            <SectionLabel>WHERE WE ARE</SectionLabel>
            <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Product built. Pilots in discussion.
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

          <div className="mt-10 flex flex-col sm:flex-row gap-4">
            <LinkButton href="/contact" variant="primary">
              Request a demo
            </LinkButton>
            <LinkButton href="/product" variant="secondary-on-light">
              See the product
            </LinkButton>
          </div>
        </div>
      </section>

      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Join us early.
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              We&apos;re looking for design-partner banks, advisors, engineers,
              and investors who want to build the financial infrastructure
              African banks have been waiting for.
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
