'use client';

/**
 * Markets hub — the consumption side of market data. Everything the canonical
 * store can serve for this bank right now: yield curves, FX spot boards,
 * issuer ratings, and macro indices, each with source attribution and
 * freshness. Source management (vendor connections, quotas, manual uploads)
 * stays in Data Engine → Market Data.
 */

import Link from 'next/link';
import { ArrowUpRight, CandlestickChart } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import { useMarketDataViews } from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';
import CurveBoard from '@/components/markets/CurveBoard';
import FxBoard from '@/components/markets/FxBoard';
import RatingsStrip from '@/components/markets/RatingsStrip';
import IndicesStrip from '@/components/markets/IndicesStrip';

const MANAGE_SOURCES_HREF = '/data-engine/market-data';

function ManageSourcesLink() {
  return (
    <Link
      href={MANAGE_SOURCES_HREF}
      className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline whitespace-nowrap"
    >
      Manage sources
      <ArrowUpRight size={13} aria-hidden />
    </Link>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-h2 text-navy">{title}</h2>
        <p className="text-caption text-slate mt-0.5">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

export default function MarketsPage() {
  const { bank } = useBankContext();
  const views = useMarketDataViews(bank?.id);
  const data = views.data;

  const isEmpty =
    data !== undefined &&
    data.curves.length === 0 &&
    data.fxRates.length === 0 &&
    data.ratings.length === 0 &&
    data.indices.length === 0;

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Markets' }]}
        title="Markets"
        subtitle="Reference rates, FX spot, curves, and ratings feeding the risk engines — with source attribution and freshness on every value."
        asOf={data ? fmtDateUTC(data.asOfDate) : undefined}
        action={<ManageSourcesLink />}
      />

      <QueryBoundary
        isLoading={views.isLoading}
        error={views.error}
        onRetry={() => views.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-8">
            {isEmpty ? (
              <EmptyState
                Icon={CandlestickChart}
                title="No market data ingested yet"
                description="The canonical store has no servable curves, FX rates, ratings, or indices for this bank. Connect a vendor source or upload the market data template in the Data Engine."
                action={
                  <Link
                    href={MANAGE_SOURCES_HREF}
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                  >
                    Open Data Engine → Market Data
                  </Link>
                }
              />
            ) : (
              <>
                {data.curves.length > 0 && (
                  <Section
                    title="Curve board"
                    subtitle="Yield curves by currency — latest arbitrated generation at or before the as-of date"
                  >
                    <CurveBoard curves={data.curves} />
                  </Section>
                )}

                {(data.fxRates.length > 0 || data.ratings.length > 0) && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
                    {data.fxRates.length > 0 && (
                      <Section
                        title="FX board"
                        subtitle="Spot per pair (quote units per 1 base) with the trailing persisted history"
                      >
                        <FxBoard fxRates={data.fxRates} />
                      </Section>
                    )}

                    {data.ratings.length > 0 && (
                      <Section
                        title="Ratings"
                        subtitle="Latest issuer rating observations with agency and watch status"
                      >
                        <RatingsStrip ratings={data.ratings} />
                      </Section>
                    )}
                  </div>
                )}

                {data.indices.length > 0 && (
                  <Section
                    title="Indicators"
                    subtitle="Macro indices and forecasts by scenario"
                  >
                    <IndicesStrip indices={data.indices} />
                  </Section>
                )}

                {(data.curves.length === 0 ||
                  data.fxRates.length === 0 ||
                  data.ratings.length === 0 ||
                  data.indices.length === 0) && (
                  <p className="text-caption text-slate">
                    Scopes without data are hidden.{' '}
                    <Link href={MANAGE_SOURCES_HREF} className="text-action hover:underline">
                      Manage market data sources
                    </Link>{' '}
                    to ingest more.
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
