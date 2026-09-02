'use client';

import Image from 'next/image';
import { useEffect, useRef, useState } from 'react';
import ProductFrame from './ProductFrame';
import { screenById } from '@/lib/product-screens';

type Module = {
  id: string;
  name: string;
  indexLine: string;
  eyebrow: string;
  title: string;
  body: string;
  /** Screen shown beside the copy; absent = placeholder panel. */
  screenId?: string;
  /** Sub-line shown in the placeholder when no screen is available. */
  placeholderHint?: string;
};

const modules: Module[] = [
  {
    id: 'liquidity',
    name: 'Liquidity',
    indexLine: 'LCR, NSFR, cash-flow stress',
    eyebrow: 'MODULE · LIQUIDITY',
    title: 'LCR and NSFR from the book, on every load.',
    body: 'HQLA, outflows, inflows, and stable funding on one engine, with shock scenarios beside the regulatory ratios. Cash-flow views that use machine learning are labeled as such, and never mixed into the ratios.',
    screenId: 'liquidity',
  },
  {
    id: 'capital',
    name: 'Regulatory Capital',
    indexLine: 'Basel III RWA, CAR headroom',
    eyebrow: 'MODULE · REGULATORY CAPITAL',
    title: 'Basel III capital, with headroom you can defend.',
    body: 'RWA under the standardized approach, the full capital stack, and CET1 / Tier 1 / CAR monitored against regulatory floors, with multi-quarter capital stress paths.',
    screenId: 'basel',
  },
  {
    id: 'credit',
    name: 'Credit',
    indexLine: 'Classification, NPL ceiling, concentration',
    eyebrow: 'MODULE · CREDIT',
    title: 'The loan book, classified and watched.',
    body: 'Five-grade and NBFI classification, provision coverage, the 10% NPL ceiling, board concentration limits, and the monthly NPL return. All computed from the same canonical book as everything else.',
    screenId: 'credit',
  },
  {
    id: 'irr',
    name: 'Interest-Rate Risk',
    indexLine: 'EVE, EaR, six IRRBB shocks',
    eyebrow: 'MODULE · INTEREST-RATE RISK',
    title: 'IRRBB across the full Basel shock set.',
    body: 'Repricing gap and duration analysis, EVE, and earnings-at-risk across the six Basel scenarios. Deterministic, and every figure traces back to the inputs that produced it.',
    screenId: 'irr',
  },
  {
    id: 'fx',
    name: 'FX Risk',
    indexLine: 'NOP limits, VaR, hedge testing',
    eyebrow: 'MODULE · FX RISK',
    title: 'Open positions, limits, and hedges in one view.',
    body: 'Net open position monitoring against single-currency and aggregate limits, historical-simulation and stressed VaR, and IFRS 9 hedge-effectiveness testing on regional pairs.',
    screenId: 'fx',
  },
  {
    id: 'ftp',
    name: 'FTP',
    indexLine: 'Matched-maturity pricing, profitability',
    eyebrow: 'MODULE · FUNDS TRANSFER PRICING',
    title: 'Transfer pricing ALCO can argue from.',
    body: 'Matched-maturity curves, behavioral modeling for non-maturity deposits, and product- and branch-level profitability, with core/volatile splits driven by reviewed behavioral duration.',
    screenId: 'ftp',
  },
  {
    id: 'forecasting',
    name: 'Forecasting',
    indexLine: 'Multi-year scenarios, what-if lab',
    eyebrow: 'MODULE · FORECASTING',
    title: 'Shock the book, re-run the real engines.',
    body: 'Multi-year projection against macro assumptions, run as immutable and reproducible snapshots. The what-if lab compares base and stressed paths with breach flags.',
    screenId: 'forecasting-whatif',
  },
];

/**
 * The module showcase: one row in the approved module-row layout (image
 * beside eyebrow / title / body), driven by the engine index below it and by
 * `#module-<id>` deep links from anywhere on the site.
 */
export default function ModuleShowcase() {
  const [activeId, setActiveId] = useState(modules[0].id);
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const applyHash = (scroll: boolean) => {
      const match = window.location.hash.match(/^#module-(.+)$/);
      if (match && modules.some((m) => m.id === match[1])) {
        setActiveId(match[1]);
        if (scroll) {
          rowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    };
    applyHash(window.location.hash.length > 0);
    const onHash = () => applyHash(true);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const active = modules.find((m) => m.id === activeId) ?? modules[0];
  const screen = active.screenId ? screenById(active.screenId) : undefined;

  return (
    <div>
      {/* Module row — the approved layout, content swaps per selection */}
      <div ref={rowRef} className="scroll-mt-24 pb-16 md:pb-[84px]">
        <div className="flex flex-col lg:flex-row-reverse gap-10 lg:gap-16 items-center">
          {screen ? (
            <ProductFrame
              key={screen.id}
              screen={screen}
              sizes="(max-width: 1024px) 100vw, 660px"
              className="w-full lg:w-[660px] shrink-0"
            />
          ) : (
            <div className="w-full lg:w-[660px] shrink-0 h-[380px] rounded-lg border-2 border-dashed border-[#C9CDD8] bg-white flex flex-col items-center justify-center gap-2.5">
              <Image
                src="/images/aequoros-mark.png"
                alt=""
                width={30}
                height={30}
                className="rounded-md"
              />
              <p className="text-sm font-semibold text-text-muted">
                {active.name} — capture pending
              </p>
              {active.placeholderHint ? (
                <p className="text-[12.5px] text-text-muted/80">
                  {active.placeholderHint}
                </p>
              ) : null}
            </div>
          )}
          <div className="flex flex-col gap-4">
            <p className="text-[12.5px] font-semibold tracking-[0.07em] text-text-muted">
              {active.eyebrow}
            </p>
            <h2 className="font-serif font-medium text-[28px] md:text-[34px] leading-[1.15] tracking-tight">
              {active.title}
            </h2>
            <p className="text-[16.5px] leading-[1.65] text-ink-soft">
              {active.body}
            </p>
          </div>
        </div>
      </div>

      {/* Engine index — click a module to open it above */}
      <div className="pb-20 md:pb-[100px]">
        <div className="border-t border-hairline pt-12">
          <p className="text-[12.5px] font-semibold tracking-[0.07em] text-text-muted mb-6">
            ALL SEVEN, ONE SPINE
          </p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {modules.map((module) => {
              const selected = module.id === active.id;
              return (
                <button
                  key={module.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => {
                    setActiveId(module.id);
                    window.history.replaceState(null, '', `#module-${module.id}`);
                    rowRef.current?.scrollIntoView({
                      behavior: 'smooth',
                      block: 'start',
                    });
                  }}
                  className={`bg-white border rounded-md px-[22px] py-5 flex flex-col gap-1.5 text-left transition-colors hover:border-navy-deep ${
                    selected ? 'border-navy-deep' : 'border-hairline'
                  }`}
                >
                  <p className="text-[15px] font-semibold">{module.name}</p>
                  <p className="text-[13.5px] leading-normal text-text-muted">
                    {module.indexLine}
                  </p>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
