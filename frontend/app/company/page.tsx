import type { Metadata } from 'next';
import Image from 'next/image';
import Kicker from '@/components/Kicker';
import PageHeader from '@/components/PageHeader';
import { LinkButton } from '@/components/Button';

export const metadata: Metadata = {
  title: 'Company — AequorOS',
  description:
    'Founded in 2025, working between Accra and Winchester, Virginia. The product is built and running; the first design-partner conversations are underway.',
};

const team = [
  {
    name: 'Eric Inkoom Danso',
    role: 'Founder & CEO · Winchester, VA / Accra',
    photo: '/images/founder.jpg',
    bio: "Quantitative analyst in capital markets and economic risk: Basel regulatory capital, RWA modeling, derivatives, and stress testing. Dual master's in Quantitative Finance and Risk Analysis, RPI. Two prior products shipped and live.",
    linkedin: 'https://linkedin.com/in/eidanso',
    linkedinLabel: 'linkedin.com/in/eidanso',
  },
  {
    name: 'Dela Anthonio',
    role: 'Chief Technology Officer · Washington DC area',
    photo: '/images/dela.jpg',
    bio: 'Software engineer focused on scalable backend systems and modernizing critical infrastructure without disrupting production. Computer Science, Virginia Tech. Leads architecture, platform engineering, and reliability.',
    linkedin: 'https://linkedin.com/in/delaanthonio',
    linkedinLabel: 'linkedin.com/in/delaanthonio',
  },
];

export default function CompanyPage() {
  return (
    <>
      <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16">
        <PageHeader
          kicker="The company"
          title="Why we're building this."
          lede="Founded in 2025, working between Accra and Winchester, Virginia. The product is built and running; the first design-partner conversations are underway."
        />

        {/* Mission */}
        <div className="grid md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] gap-8 md:gap-20 pb-16 md:pb-20">
          <h2 className="font-serif font-medium text-[28px] md:text-[34px] leading-[1.15] tracking-tight">
            Africa&apos;s banks deserve infrastructure built for their world.
          </h2>
          <div className="flex flex-col gap-4">
            <p className="text-[16.5px] leading-[1.7] text-ink-soft">
              Africa&apos;s banking sector manages more than two trillion
              dollars in assets, largely on infrastructure designed for a
              different context: large institutions, stable currencies, mature
              regulatory regimes. The tools never quite fit, so most banks fell
              back on spreadsheets and consultants.
            </p>
            <p className="text-[16.5px] leading-[1.7] text-ink-soft">
              We think a mid-tier African bank deserves the computational
              backbone a Tier 1 institution takes for granted, at a price it
              can justify. A bank that can see its liquidity, capital, and risk
              clearly can lend more, serve more customers, and stand firmer
              through a shock.
            </p>
          </div>
        </div>

        {/* Founder pull quote */}
        <div className="pb-16 md:pb-20">
          <blockquote className="bg-navy-deep rounded-md px-8 md:px-14 py-12 flex flex-col gap-4">
            <p className="font-serif text-xl md:text-[27px] leading-[1.4] text-white max-w-4xl">
              &ldquo;African banks are being asked for Tier 1 rigor with
              spreadsheet tools. We build the computational backbone that lets
              them manage liquidity, capital, and risk safely &mdash; at a
              price a mid-tier bank can actually afford.&rdquo;
            </p>
            <cite className="not-italic text-sm text-white/65">
              Eric Inkoom Danso · Founder &amp; CEO
            </cite>
          </blockquote>
        </div>

        {/* Team */}
        <div className="pb-16 md:pb-20">
          <div className="flex flex-col gap-3.5 mb-10 max-w-2xl">
            <Kicker>The team</Kicker>
            <h2 className="font-serif font-medium text-3xl md:text-[38px] leading-[1.12] tracking-tight">
              Verifiable on LinkedIn, reachable by email.
            </h2>
          </div>
          <div className="grid md:grid-cols-2 gap-7">
            {team.map((member) => (
              <div
                key={member.name}
                className="bg-white border border-hairline rounded-md p-7 md:p-8 flex flex-col sm:flex-row gap-6"
              >
                <Image
                  src={member.photo}
                  alt={member.name}
                  width={120}
                  height={120}
                  sizes="120px"
                  className="h-[120px] w-[120px] rounded-md object-cover shrink-0"
                />
                <div className="flex flex-col gap-2">
                  <p className="text-[19px] font-semibold text-ink">
                    {member.name}
                  </p>
                  <p className="text-[13.5px] text-text-muted">{member.role}</p>
                  <p className="text-[14.5px] leading-[1.6] text-ink-soft">
                    {member.bio}
                  </p>
                  <a
                    href={member.linkedin}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[13.5px] font-medium text-action hover:text-action-dark transition-colors"
                  >
                    {member.linkedinLabel}
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Status + join */}
        <div className="border-t border-hairline pt-14 pb-24 flex flex-col md:flex-row md:items-center md:justify-between gap-10">
          <div className="flex flex-col gap-2.5 max-w-2xl">
            <h2 className="font-serif font-medium text-[28px] md:text-[34px] leading-[1.15] tracking-tight">
              Join us early.
            </h2>
            <p className="text-[16px] leading-[1.65] text-ink-soft">
              The working platform is public on this site; pilots are in
              discussion. We&apos;re looking for design-partner banks,
              advisors, engineers, and investors who want to build this with
              us.
            </p>
          </div>
          <LinkButton href="/contact" variant="primary" className="shrink-0">
            Start a conversation
          </LinkButton>
        </div>
      </div>
    </>
  );
}
