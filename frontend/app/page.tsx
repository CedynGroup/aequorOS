import Link from 'next/link';
import Kicker from '@/components/Kicker';
import ProductFrame from '@/components/ProductFrame';
import { LinkButton } from '@/components/Button';
import { heroScreen, homepageFeatureScreens } from '@/lib/product-screens';

const statusChips = [
  { dot: 'bg-live', label: 'Platform live' },
  { dot: 'bg-accent', label: 'Bank of Ghana return formats' },
  { dot: 'bg-watch', label: 'Pilot cohort forming' },
];

const timeline = [
  {
    date: 'SEPT 2025',
    text: 'Credit concentration guidelines issued for banks, savings & loans, and finance houses.',
    terminal: false,
  },
  {
    date: 'FEB 2026',
    text: 'ICAAP, stress-testing, and two liquidity directives published for exposure.',
    terminal: false,
  },
  {
    date: 'DEC 2026',
    text: 'NPL ratios must reach 10% or below. Board concentration frameworks due.',
    terminal: false,
  },
  {
    date: '1 JAN 2027',
    text: 'Stated effective date across the new directives. Tier 1 rigor, every institution.',
    terminal: true,
  },
];

const engines = [
  { name: 'Liquidity', href: '/product#module-liquidity' },
  { name: 'Capital', href: '/product#module-capital' },
  { name: 'Credit', href: '/product#module-credit' },
  { name: 'Interest-rate risk', href: '/product#module-irr' },
  { name: 'FX', href: '/product#module-fx' },
  { name: 'FTP', href: '/product#module-ftp' },
  { name: 'Forecasting', href: '/product#module-forecasting' },
];

const ledger = [
  {
    dot: 'bg-live',
    title: 'LIVE TODAY',
    rows: [
      'Data Engine with file upload and secure API push',
      'Seven calculation engines on one canonical book',
      'Bank of Ghana BSD returns, generated and export-ready',
      'Full audit trail, lineage, and reproducible runs',
    ],
  },
  {
    dot: 'bg-watch',
    title: 'SET UP PER BANK',
    rows: [
      'Direct core-banking extracts, mapped to your chart of accounts during onboarding',
      'Market-data vendor feeds, enabled after vendor onboarding checks',
      "Behavioral models tuned to your institution's own history",
    ],
  },
  {
    dot: 'bg-accent',
    title: "WHAT'S NEXT",
    rows: [
      'A first cohort of design-partner banks, onboarding now',
      'Independent security audit ahead of production banking data',
      'Nigeria (CBN) and South Africa (SARB) return formats on the same engine',
    ],
  },
];

export default function HomePage() {
  return (
    <>
      {/* ============ Hero — the one dark band ============ */}
      <section className="relative overflow-hidden bg-navy-deep text-white">
        <svg
          width="520"
          height="520"
          viewBox="0 0 520 520"
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-40 opacity-[0.06]"
        >
          <path d="M260 0 L520 520 H400 L260 220 L120 520 H0 Z" fill="#4FC3F7" />
        </svg>
        <div className="relative max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pt-14 md:pt-20">
          <div className="grid lg:grid-cols-[minmax(0,10fr)_minmax(0,11fr)] gap-12 lg:gap-14 items-end">
            <div className="flex flex-col gap-7 pb-14 lg:pb-24">
              <div className="flex flex-wrap gap-2.5">
                {statusChips.map((chip) => (
                  <span
                    key={chip.label}
                    className="inline-flex items-center gap-2 h-[30px] px-3.5 rounded-full border border-white/[0.22] text-[12.5px] font-medium text-white/85"
                  >
                    <span className={`h-[7px] w-[7px] rounded-full ${chip.dot}`} />
                    {chip.label}
                  </span>
                ))}
              </div>
              <h1 className="font-serif font-medium text-[42px] md:text-[56px] lg:text-[64px] leading-[1.06] tracking-tight">
                Treasury and ALM infrastructure for African banks.
              </h1>
              <p className="text-lg leading-relaxed text-white/[0.78] max-w-[560px]">
                AequorOS turns core banking data into risk numbers, board
                answers, and central-bank returns. Feed it a file, a push API,
                or a read-only view of the core you already run. Every figure
                shows its work.
              </p>
              <div className="flex flex-wrap items-center gap-3.5">
                <LinkButton href="/contact" variant="primary-on-dark">
                  Request a demo
                </LinkButton>
                <LinkButton href="/product" variant="secondary">
                  See the product live
                </LinkButton>
              </div>
              <p className="text-[13.5px] text-white/50">
                The full product interface is public on this site. No login, no
                form.
              </p>
            </div>
            <Link
              href="/product"
              className="hidden lg:block -mb-px"
            >
              <ProductFrame
                screen={heroScreen}
                variant="chrome-dark"
                priority
                sizes="(max-width: 1024px) 100vw, 640px"
              />
            </Link>
          </div>
        </div>
      </section>

      {/* ============ The problem ============ */}
      <section className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pt-20 md:pt-24 pb-16 md:pb-20">
        <div className="grid md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] gap-10 md:gap-20">
          <div className="flex flex-col gap-5">
            <Kicker>The gap</Kicker>
            <h2 className="font-serif font-medium text-3xl md:text-[42px] leading-[1.12] tracking-tight">
              Billions under management. Spreadsheets under the hood.
            </h2>
          </div>
          <div className="flex flex-col gap-4 md:pt-12">
            <p className="text-[17px] leading-[1.7] text-ink-soft">
              Across Ghana, Nigeria, and Kenya, mid-tier banks still run
              asset-liability management on Excel workbooks and an annual
              consulting engagement. The global vendors that solve this charge
              Tier 1 prices and take the better part of a year to install. So
              the banks in the middle make do.
            </p>
            <p className="text-[17px] leading-[1.7] text-ink-soft">
              Regulators are done waiting. Basel discipline, monthly prudential
              returns, and real stress testing are landing on every
              deposit-taking institution, on dates already published in
              circulars and exposure drafts.
            </p>
          </div>
        </div>
      </section>

      {/* ============ Regulator timeline ============ */}
      <section className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-20 md:pb-24">
        <div className="bg-white border border-hairline rounded-md p-8 md:p-12">
          <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2 mb-10">
            <h2 className="font-serif font-medium text-2xl md:text-3xl tracking-tight">
              Bank of Ghana has set the clock.
            </h2>
            <p className="text-[13.5px] text-text-muted">
              Public instruments and exposure drafts, as published
            </p>
          </div>
          <div className="relative hidden md:block">
            <div className="absolute left-1.5 right-1.5 top-[7px] h-[3px] bg-hairline" />
            <div className="relative grid grid-cols-4 gap-8">
              {timeline.map((item) => (
                <span
                  key={item.date}
                  className={
                    item.terminal
                      ? 'h-[17px] w-[17px] rounded-full bg-kicker border-[3px] border-white'
                      : 'mt-0.5 h-[13px] w-[13px] rounded-full bg-navy-deep'
                  }
                />
              ))}
            </div>
          </div>
          <div className="grid md:grid-cols-4 gap-6 md:gap-8 md:mt-7">
            {timeline.map((item) => (
              <div key={item.date} className="flex flex-col gap-2">
                <p
                  className={`text-[13px] font-semibold tracking-[0.04em] ${
                    item.terminal ? 'text-kicker' : 'text-navy-deep'
                  }`}
                >
                  {item.date}
                </p>
                <p className="text-[14.5px] leading-[1.55] text-ink-soft">
                  {item.text}
                </p>
              </div>
            ))}
          </div>
          <p className="mt-9 pt-6 border-t border-stone font-serif italic text-[15px] text-text-muted">
            A bank that waits for the deadline will meet it in a spreadsheet.
          </p>
        </div>
      </section>

      {/* ============ One governed path ============ */}
      <section className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-20 md:pb-28">
        <div className="flex flex-col gap-4 mb-12 max-w-2xl">
          <Kicker>What it is</Kicker>
          <h2 className="font-serif font-medium text-3xl md:text-[42px] leading-[1.12] tracking-tight">
            One governed path from core to return.
          </h2>
          <p className="text-[17px] leading-relaxed text-ink-soft">
            Data lands once. Everything downstream recomputes automatically,
            and every number can be traced back to the load that produced it.
          </p>
        </div>
        <div className="flex flex-col xl:flex-row xl:items-stretch gap-4 xl:gap-0">
          <div className="xl:w-[230px] bg-white border border-hairline rounded-md p-6 flex flex-col gap-2.5">
            <p className="text-xs font-semibold tracking-[0.06em] text-text-muted">
              YOUR SOURCES
            </p>
            <p className="text-[15px] font-medium">Core banking extract</p>
            <p className="text-[15px] font-medium">File upload</p>
            <p className="text-[15px] font-medium">Secure push API</p>
          </div>
          <FlowArrow />
          <div className="xl:w-[225px] bg-navy-deep rounded-md p-6 flex flex-col gap-2.5">
            <p className="text-xs font-semibold tracking-[0.06em] text-accent">
              DATA ENGINE
            </p>
            <p className="text-[15px] font-medium text-white">
              Normalize and validate
            </p>
            <p className="text-[15px] font-medium text-white">Canonical model</p>
            <p className="text-[15px] font-medium text-white">Full lineage</p>
          </div>
          <FlowArrow />
          <div className="flex-1 bg-white border border-hairline rounded-md p-6 flex flex-col gap-3.5">
            <p className="text-xs font-semibold tracking-[0.06em] text-text-muted">
              SEVEN ENGINES
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {engines.map((engine) => (
                <Link
                  key={engine.name}
                  href={engine.href}
                  className="inline-flex h-9 items-center justify-center rounded border border-hairline text-[13.5px] font-medium transition-colors hover:border-navy-deep"
                >
                  {engine.name}
                </Link>
              ))}
            </div>
            <p className="text-[13.5px] text-text-muted">
              Recomputed on every accepted load. Deterministic where regulators
              require it.
            </p>
          </div>
          <FlowArrow />
          <div className="xl:w-[230px] bg-white border border-hairline rounded-md p-6 flex flex-col gap-2.5">
            <p className="text-xs font-semibold tracking-[0.06em] text-text-muted">
              THE OUTPUT
            </p>
            <p className="text-[15px] font-medium">BoG returns, sign-ready</p>
            <p className="text-[15px] font-medium">Board packs</p>
            <p className="text-[15px] font-medium">ALCO views</p>
          </div>
        </div>
      </section>

      {/* ============ Product screens ============ */}
      <section className="bg-stone">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6 mb-12">
            <div className="flex flex-col gap-3.5 max-w-2xl">
              <Kicker>Proof, not promises</Kicker>
              <h2 className="font-serif font-medium text-3xl md:text-[42px] leading-[1.12] tracking-tight">
                The working product, in public.
              </h2>
              <p className="text-[17px] leading-relaxed text-ink-soft">
                These screens are the live platform running a synthetic
                Ghanaian bank. Browse all of it without an account.
              </p>
            </div>
            <Link
              href="/product"
              className="text-[15px] font-semibold text-action hover:text-action-dark transition-colors shrink-0"
            >
              Browse the product interface &rarr;
            </Link>
          </div>
          <div className="grid md:grid-cols-2 gap-9">
            {homepageFeatureScreens.map((screen, i) => (
              <Link
                key={screen.id}
                href={screen.id === 'data-engine' ? '/product#data-engine' : '/product#governance'}
                className="flex flex-col gap-4 group"
              >
                <ProductFrame
                  screen={screen}
                  priority={i === 0}
                  sizes="(max-width: 768px) 100vw, 620px"
                  className="transition-transform group-hover:-translate-y-0.5"
                />
                <p className="text-sm leading-relaxed">
                  <span className="font-semibold text-ink">{screen.title}</span>{' '}
                  <span className="text-text-muted">{screen.caption}</span>
                </p>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* ============ Where we are, plainly ============ */}
      <section className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-20 md:py-24">
        <div className="flex flex-col gap-3.5 mb-12 max-w-2xl">
          <Kicker>Straight answers</Kicker>
          <h2 className="font-serif font-medium text-3xl md:text-[42px] leading-[1.12] tracking-tight">
            Where we are, plainly.
          </h2>
        </div>
        <div className="grid md:grid-cols-3 gap-7">
          {ledger.map((col) => (
            <div
              key={col.title}
              className="bg-white border border-hairline rounded-md p-7 flex flex-col gap-4"
            >
              <div className="flex items-center gap-2.5">
                <span className={`h-[9px] w-[9px] rounded-full ${col.dot}`} />
                <p className="text-[13px] font-semibold tracking-[0.06em] text-ink">
                  {col.title}
                </p>
              </div>
              <div className="flex flex-col gap-2.5">
                {col.rows.map((row) => (
                  <p key={row} className="text-[15px] leading-normal text-ink-soft">
                    {row}
                  </p>
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-7 font-serif italic text-[15px] text-text-muted">
          We would rather you hear this from us than find it in diligence.
        </p>
      </section>

      {/* ============ Closing CTA ============ */}
      <section className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-24 md:pb-28">
        <div className="border-t border-hairline pt-16 md:pt-20 flex flex-col items-center gap-5 text-center">
          <h2 className="font-serif font-medium text-4xl md:text-[52px] leading-[1.1] tracking-tight max-w-3xl">
            Bring us your hardest reporting month.
          </h2>
          <p className="text-lg leading-relaxed text-ink-soft max-w-xl">
            Thirty minutes with your Treasury or Risk lead. We will walk the
            platform against the workflows your bank actually runs.
          </p>
          <div className="mt-2 flex flex-wrap items-center justify-center gap-4">
            <LinkButton href="/contact" variant="primary">
              Request a demo
            </LinkButton>
            <Link
              href="/product"
              className="text-[15px] font-medium text-action hover:text-action-dark transition-colors"
            >
              Or browse the product first
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}

function FlowArrow() {
  return (
    <div className="flex items-center justify-center px-2.5 py-1 rotate-90 xl:rotate-0 self-center">
      <svg width="28" height="14" viewBox="0 0 28 14" aria-hidden>
        <path
          d="M0 7 H22 M17 1.5 L23.5 7 L17 12.5"
          stroke="#B9BDC9"
          strokeWidth="2"
          fill="none"
        />
      </svg>
    </div>
  );
}
