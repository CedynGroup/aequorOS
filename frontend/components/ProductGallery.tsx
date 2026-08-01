'use client';

import { useMemo, useState } from 'react';
import Image from 'next/image';
import {
  productScreens,
  productWalkthroughIds,
  type ProductScreen,
} from '@/lib/product-screens';
import ProductFrame from './ProductFrame';

function orderedWalkthrough(): ProductScreen[] {
  const byId = new Map(productScreens.map((s) => [s.id, s]));
  return productWalkthroughIds
    .map((id) => byId.get(id))
    .filter((s): s is ProductScreen => Boolean(s));
}

/**
 * Interactive product tour — public UI proof without a login wall.
 */
export default function ProductGallery() {
  const screens = useMemo(() => orderedWalkthrough(), []);
  const [activeId, setActiveId] = useState(screens[0]?.id ?? 'command-center');
  const active = screens.find((s) => s.id === activeId) ?? screens[0];

  if (!active) return null;

  return (
    <div>
      <div className="flex gap-2 overflow-x-auto pb-2 -mx-1 px-1 scrollbar-thin">
        {screens.map((screen) => {
          const selected = screen.id === active.id;
          return (
            <button
              key={screen.id}
              type="button"
              onClick={() => setActiveId(screen.id)}
              aria-pressed={selected}
              className={`shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 ${
                selected
                  ? 'bg-navy text-white'
                  : 'bg-white text-text-primary border border-border-light hover:border-navy/30'
              }`}
            >
              {screen.label}
            </button>
          );
        })}
      </div>

      <div className="mt-6 grid lg:grid-cols-[1fr,280px] gap-8 items-start">
        <ProductFrame screen={active} tone="light" showCaption />

        <div className="hidden lg:grid grid-cols-2 gap-3">
          {screens.map((screen) => {
            const selected = screen.id === active.id;
            return (
              <button
                key={screen.id}
                type="button"
                onClick={() => setActiveId(screen.id)}
                aria-label={`Show ${screen.title}`}
                aria-pressed={selected}
                className={`relative aspect-[16/10] overflow-hidden rounded-lg border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  selected
                    ? 'border-accent ring-2 ring-accent/40'
                    : 'border-border-light hover:border-navy/30'
                }`}
              >
                <Image
                  src={screen.src}
                  alt=""
                  width={480}
                  height={300}
                  className="object-cover object-top w-full h-full"
                  sizes="140px"
                />
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
