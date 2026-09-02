'use client';

import { useState } from 'react';
import ProductFrame from './ProductFrame';
import { screenById } from '@/lib/product-screens';

export type ScreenTabItem = {
  tab: string;
  eyebrow: string;
  title: string;
  body: string;
  screenId: string;
};

/**
 * Tabbed screen section: pick a tab, its copy sits left and its screenshot
 * right — the same row layout as the module showcase.
 */
export default function ScreenTabs({ items }: { items: ScreenTabItem[] }) {
  const [activeIdx, setActiveIdx] = useState(0);
  if (items.length === 0) return null;
  const active = items[activeIdx] ?? items[0];
  const screen = screenById(active.screenId);

  return (
    <div>
      <div className="flex flex-wrap gap-2.5 mb-8">
        {items.map((item, idx) => {
          const selected = idx === activeIdx;
          return (
            <button
              key={item.tab}
              type="button"
              aria-pressed={selected}
              onClick={() => setActiveIdx(idx)}
              className={`inline-flex h-10 items-center rounded px-4 text-[14px] font-medium border transition-colors ${
                selected
                  ? 'border-navy-deep bg-navy-deep text-white'
                  : 'border-hairline bg-white text-ink hover:border-navy-deep'
              }`}
            >
              {item.tab}
            </button>
          );
        })}
      </div>
      <div className="flex flex-col lg:flex-row-reverse gap-10 lg:gap-16 items-center">
        {screen ? (
          <ProductFrame
            key={screen.id}
            screen={screen}
            sizes="(max-width: 1024px) 100vw, 660px"
            className="w-full lg:w-[660px] shrink-0"
          />
        ) : null}
        <div className="flex flex-col gap-4">
          <p className="text-[12.5px] font-semibold tracking-[0.07em] text-text-muted">
            {active.eyebrow}
          </p>
          <h3 className="font-serif font-medium text-[26px] md:text-[32px] leading-[1.15] tracking-tight">
            {active.title}
          </h3>
          <p className="text-[16.5px] leading-[1.65] text-ink-soft">
            {active.body}
          </p>
        </div>
      </div>
    </div>
  );
}
