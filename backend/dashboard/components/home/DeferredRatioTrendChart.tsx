'use client';

import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';
import ChartFrame from '@/components/ui/ChartFrame';
import { useModuleScope } from '@/components/shell/BankContext';

const RatioTrendChart = dynamic(() => import('./RatioTrendChart'), {
  ssr: false,
  loading: RatioTrendChartSkeleton,
});

/**
 * Keep Recharts out of the Command Center entry graph until its below-the-fold
 * panel approaches the viewport. The generous margin starts the request before
 * the user reaches the panel without competing with the primary cockpit load.
 */
export default function DeferredRatioTrendChart({
  bankId,
  periodId,
}: {
  bankId: string | undefined;
  periodId: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [shouldLoad, setShouldLoad] = useState(false);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel || shouldLoad) return;

    if (!('IntersectionObserver' in window)) {
      setShouldLoad(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setShouldLoad(true);
        observer.disconnect();
      },
      { rootMargin: '320px 0px' },
    );

    observer.observe(panel);
    return () => observer.disconnect();
  }, [shouldLoad]);

  return (
    <div ref={panelRef}>
      {shouldLoad ? (
        <RatioTrendChart bankId={bankId} periodId={periodId} />
      ) : (
        <RatioTrendChartSkeleton />
      )}
    </div>
  );
}

/** Matches the loaded frame's fixed geometry so the async swap cannot shift it. */
function RatioTrendChartSkeleton() {
  const isSdi = useModuleScope().institutionClass === 'sdi';

  return (
    <ChartFrame
      title="Ratio trend"
      subtitle={
        isSdi
          ? 'CAR (s.29) per reporting period'
          : 'LCR & NSFR (left axis) · CAR (right axis) per reporting period'
      }
      height={280}
      loading
      actions={
        <div
          className="inline-flex items-center rounded-md border border-border-light bg-surface p-0.5 animate-pulse"
          aria-hidden
        >
          {['3M', '6M', '1Y', 'All'].map((label) => (
            <span key={label} className="invisible px-2.5 py-1 text-micro">
              {label}
            </span>
          ))}
        </div>
      }
      footer={
        <span className="invisible" aria-hidden>
          {isSdi
            ? '0 periods · 0 with stored results'
            : '0 periods · LCR +0.0pp over the window · 0 with stored results'}
        </span>
      }
    >
      {null}
    </ChartFrame>
  );
}
