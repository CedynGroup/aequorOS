import type { Metadata } from 'next';
import SectionLabel from '@/components/SectionLabel';
import { LinkButton } from '@/components/Button';

export const metadata: Metadata = {
  title: 'Investor Materials — AequorOS',
  description:
    'Access the AequorOS pitch deck and financial model. We are raising a $1.25M seed round to build Treasury and ALM infrastructure for African banks.',
};

const materials = [
  {
    label: 'PITCH DECK',
    title: 'Investor Presentation',
    description:
      'Overview of the problem, solution, market opportunity, business model, team, and fundraising ask.',
    fileType: 'PDF',
    href: process.env.NEXT_PUBLIC_INVESTOR_PITCH_DECK_URL ?? '#',
  },
  {
    label: 'FINANCIAL MODEL',
    title: 'Five-Year Financial Model',
    description:
      'Revenue projections, unit economics, headcount plan, and 18-month use-of-funds breakdown.',
    fileType: 'Excel',
    href: process.env.NEXT_PUBLIC_INVESTOR_FINANCIAL_MODEL_URL ?? '#',
  },
];

export default function InvestorsPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-28">
          <div className="max-w-3xl">
            <SectionLabel>INVESTOR MATERIALS</SectionLabel>
            <h1 className="mt-6 font-serif font-bold text-white text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              We are raising a $1.25M seed round.
            </h1>
            <p className="mt-8 text-ice-blue text-lg md:text-xl leading-relaxed max-w-[600px]">
              AequorOS is building cloud-native Treasury and ALM infrastructure
              for mid-tier African banks. Our seed round funds product
              validation, MVP development, and our first five pilot banks.
            </p>
          </div>
        </div>
      </section>

      {/* Materials */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <SectionLabel>DOCUMENTS</SectionLabel>
          <h2 className="mt-6 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
            Access our materials directly.
          </h2>
          <p className="mt-4 text-text-muted text-base md:text-lg leading-relaxed max-w-[600px]">
            Both documents open in Google Drive. No sign-in required.
          </p>

          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {materials.map((item) => (
              <a
                key={item.label}
                href={item.href}
                target="_blank"
                rel="noreferrer"
                className="group flex flex-col justify-between bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-8 hover:shadow-md transition-shadow"
              >
                <div>
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-accent text-xs font-semibold tracking-[0.15em]">
                      {item.label}
                    </span>
                    <span className="text-text-muted text-xs font-medium uppercase tracking-wide border border-border-light rounded px-2 py-0.5">
                      {item.fileType}
                    </span>
                  </div>
                  <h3 className="mt-4 font-serif font-bold text-navy text-xl group-hover:text-accent transition-colors">
                    {item.title}
                  </h3>
                  <p className="mt-3 text-text-primary leading-relaxed text-sm md:text-base">
                    {item.description}
                  </p>
                </div>
                <div className="mt-8 flex items-center gap-2 text-accent text-sm font-semibold">
                  Open in Google Drive
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                    className="transition-transform group-hover:translate-x-1"
                  >
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    <polyline points="15 3 21 3 21 9" />
                    <line x1="10" y1="14" x2="21" y2="3" />
                  </svg>
                </div>
              </a>
            ))}
          </div>
        </div>
      </section>

      {/* Round details */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <SectionLabel>THE RAISE</SectionLabel>
          <div className="mt-8 grid gap-6 md:grid-cols-3">
            {[
              { stat: '$1.25M', label: 'Seed round target' },
              { stat: '18 months', label: 'Runway to first 5 pilot banks' },
              { stat: 'Pre-seed', label: 'Current stage' },
            ].map(({ stat, label }) => (
              <div
                key={label}
                className="bg-white border border-border-light rounded-lg p-8"
              >
                <p className="font-serif font-bold text-navy text-4xl">{stat}</p>
                <p className="mt-3 text-text-muted text-sm leading-relaxed">
                  {label}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="bg-navy-deep text-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="max-w-[800px] mx-auto text-center">
            <h2 className="font-serif font-bold text-white text-3xl md:text-4xl leading-tight">
              Ready to talk?
            </h2>
            <p className="mt-6 text-ice-blue text-lg leading-relaxed">
              Reach out directly to the founder. We respond to every
              serious inquiry.
            </p>
            <div className="mt-10 flex justify-center">
              <LinkButton href="/contact" variant="primary-on-dark">
                Start a conversation
              </LinkButton>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
