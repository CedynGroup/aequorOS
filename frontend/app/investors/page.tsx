import type { Metadata } from 'next';
import PageHeader from '@/components/PageHeader';

export const metadata: Metadata = {
  title: 'Investors — AequorOS',
  description:
    'AequorOS is raising a $500K seed round: Treasury and ALM infrastructure for African banks, starting from a working product. Pitch deck and financial model available.',
};

const thesis = [
  {
    label: 'WORKING PRODUCT',
    body: "Seven calculation engines, a governed data spine, and Bank of Ghana returns generated in the regulator's own formats. The interface is public on this site.",
  },
  {
    label: 'REGULATORY TAILWIND',
    body: "Ghana's new prudential directives carry a stated effective date of 1 January 2027, and other African regulators are moving the same way. Compliance is the wedge.",
  },
  {
    label: 'FOUNDER-LED',
    body: 'A quantitative-risk founder and a systems CTO, both verifiable on LinkedIn. Ghanaian roots, US market discipline, building in public.',
  },
];

const pitchDeckUrl = process.env.NEXT_PUBLIC_INVESTOR_PITCH_DECK_URL ?? null;
const financialModelUrl =
  process.env.NEXT_PUBLIC_INVESTOR_FINANCIAL_MODEL_URL ?? null;

export default function InvestorsPage() {
  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16">
      <PageHeader
        kicker="Investors"
        title="The balance-sheet layer for African banking."
        lede="Two trillion dollars in assets, thousands of institutions, and a regulatory wave that makes modern ALM mandatory rather than optional. We are building the infrastructure those banks will run on, starting from a working product rather than a deck."
        maxWidth="max-w-4xl"
      />

      <div className="grid md:grid-cols-3 gap-6 pb-14">
        {thesis.map((item) => (
          <div
            key={item.label}
            className="bg-white border border-hairline rounded-md p-7 flex flex-col gap-2.5"
          >
            <p className="text-[13px] font-semibold tracking-[0.06em] text-text-muted">
              {item.label}
            </p>
            <p className="text-[15px] leading-[1.6] text-ink-soft">{item.body}</p>
          </div>
        ))}
      </div>

      <div className="pb-24">
        <div className="bg-navy-deep rounded-md px-8 md:px-12 py-10 md:py-11 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-8">
          <div className="flex flex-col gap-2 max-w-2xl">
            <h2 className="font-serif font-medium text-[26px] md:text-[30px] tracking-tight text-white">
              Raising our seed round.
            </h2>
            <p className="text-[15.5px] leading-relaxed text-white/[0.72]">
              The deck and the financial model are available on request, and
              the best diligence is the product itself.
            </p>
          </div>
          <div className="flex flex-wrap gap-3.5 shrink-0">
            {pitchDeckUrl ? (
              <a
                href={pitchDeckUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-12 items-center rounded bg-white px-6 text-[14.5px] font-semibold text-navy-deep hover:bg-ice-blue transition-colors"
              >
                Pitch deck
              </a>
            ) : null}
            {financialModelUrl ? (
              <a
                href={financialModelUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-12 items-center rounded border border-white/35 px-6 text-[14.5px] font-medium text-white hover:bg-white/10 transition-colors"
              >
                Financial model
              </a>
            ) : null}
            <a
              href="mailto:eric@aequoros.com"
              className="inline-flex h-12 items-center rounded border border-white/35 px-6 text-[14.5px] font-medium text-white hover:bg-white/10 transition-colors"
            >
              Request the materials
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
