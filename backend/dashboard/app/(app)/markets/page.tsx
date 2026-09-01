'use client';

/**
 * Markets hub — the consumption side of market data, as a tabbed enterprise
 * surface (spec docs/internal/market_data_sources.md §5). Everything the
 * canonical store can serve for this bank right now: published yield curves
 * (multi-curve, with the bank's private overlay composition), grouped reference
 * rates, FX spot boards, issuer ratings, and macro indices — each with source
 * attribution and freshness — plus the desk's published forward grids and the
 * three-plane source control room. Source management (vendor connections,
 * quotas, manual uploads) stays in Data Engine → Market Data.
 *
 * Tabs: Overview · Curves · Market data · Sources. Market data contains the
 * Forward curves, Rates, and FX sub-navigation; the as-of scrubber reproduces
 * the complete surface as published then.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, CalendarClock, CandlestickChart } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import SubTabs from '@/components/ui/SubTabs';
import { useBankContext } from '@/components/shell/BankContext';
import SdiModuleContext from '@/components/sdi/SdiModuleContext';
import {
  useLiveSnapshots,
  useLiveSummary,
  useMarketDataSourcePreferences,
  useMarketDataViews,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, isoDate } from '@/lib/api/values';
import CurveBoard from '@/components/markets/CurveBoard';
import CurveThumbnails from '@/components/markets/CurveThumbnails';
import CurvesExplorer from '@/components/markets/CurvesExplorer';
import ForwardTab from '@/components/markets/ForwardTab';
import FxBoard from '@/components/markets/FxBoard';
import FxForwardsBoard from '@/components/markets/FxForwardsBoard';
import ImpliedRatingCard from '@/components/markets/ImpliedRatingCard';
import SdiFinancialStrengthCard from '@/components/markets/SdiFinancialStrengthCard';
import SdiFinancialStrengthTrend from '@/components/markets/SdiFinancialStrengthTrend';
import RatingsStrip from '@/components/markets/RatingsStrip';
import IndicesStrip from '@/components/markets/IndicesStrip';
import RatesBoard, { isReferenceRateCode } from '@/components/markets/RatesBoard';
import OverlayDrawer from '@/components/markets/OverlayDrawer';
import SourceIndicator from '@/components/markets/SourceIndicator';
import SourcesControlRoom from '@/components/markets/SourcesControlRoom';

const MANAGE_SOURCES_HREF = '/data-engine/market-data';

type TabKey = 'overview' | 'curves' | 'market-data' | 'sources';
type MarketDataView = 'forward' | 'rates' | 'fx';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'curves', label: 'Curves' },
  { key: 'market-data', label: 'Market data' },
  { key: 'sources', label: 'Sources' },
];

const MARKET_DATA_TABS: { key: MarketDataView; label: string }[] = [
  { key: 'forward', label: 'Forward curves' },
  { key: 'rates', label: 'Rates' },
  { key: 'fx', label: 'FX' },
];

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

/**
 * As-of scrubber (FC-5 §4.2): reproduces the whole Markets surface as it was
 * published at the chosen date. Picking today (or clearing) returns to the live
 * latest-published view.
 */
function AsOfControl({
  asOf,
  todayIso,
  onChange,
}: {
  asOf: string | null;
  todayIso: string;
  onChange: (value: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <label className="inline-flex items-center gap-1.5 text-caption text-slate whitespace-nowrap">
        As of
        <input
          type="date"
          value={asOf ?? todayIso}
          max={todayIso}
          onChange={(event) => {
            const value = event.target.value;
            onChange(!value || value >= todayIso ? null : value);
          }}
          className="px-2 py-1 text-caption font-mono bg-surface border border-border rounded text-navy"
        />
      </label>
      {asOf !== null && (
        <button
          type="button"
          onClick={() => onChange(null)}
          className="text-caption font-medium text-action hover:underline whitespace-nowrap"
        >
          Today
        </button>
      )}
    </div>
  );
}

function ReproductionBanner({ asOfDate }: { asOfDate: Date }) {
  return (
    <div className="rounded-lg border border-warning/30 bg-warning-light px-4 py-2.5 flex items-center gap-2 text-caption text-navy">
      <CalendarClock size={14} className="text-warning shrink-0" aria-hidden />
      <span>
        Reproducing the Markets surface as published on{' '}
        <span className="font-mono font-medium">{fmtDateUTC(asOfDate)}</span>. Every value is the
        golden copy as it stood then — not re-derived.
      </span>
    </div>
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
  const [asOf, setAsOf] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>('overview');
  const [marketDataView, setMarketDataView] = useState<MarketDataView>('forward');
  const [overlayCurveName, setOverlayCurveName] = useState<string | null>(null);
  const [selectedCurveName, setSelectedCurveName] = useState<string | null>(null);

  const views = useMarketDataViews(bank?.id, asOf ?? undefined);
  const liveSummary = useLiveSummary(bank?.id);
  const prefs = useMarketDataSourcePreferences(bank?.id);
  const data = views.data;

  const todayIso = isoDate(new Date());
  const isReproduction = asOf !== null && asOf < todayIso;
  const liveRating = isReproduction
    ? undefined
    : liveSummary.data?.modules.find((module) => module.module === 'rating');
  const sdiFinancialStrength =
    liveRating?.metrics.assessment_kind === 'sdi_financial_strength';
  // The plane-2 daily ladder for the rating module. Already exposed by
  // ``GET /banks/{id}/live-snapshots`` — the trend is a read of what was
  // recorded each day, not a recomputation of history.
  const ratingLadder = useLiveSnapshots(bank?.id, 'rating', 45);
  // The SDI scorecard has THREE unavailable states and they mean different
  // things to an operator. Rendering one message for all of them told a reader
  // the methodology was awaiting approval when it had already been approved and
  // the real gap was missing evidence at the anchored date.
  //   methodology_pending — no approved AEQ-GH-SDI-FS version exists yet
  //   not_computable      — approved, but a mandatory component has no evidence
  //                         AT THIS as-of date (omitted, never scored neutral)
  const sdiAssessmentState =
    typeof liveRating?.metrics.assessment_state === 'string'
      ? liveRating.metrics.assessment_state
      : undefined;
  const sdiMethodologyPending =
    sdiFinancialStrength && sdiAssessmentState === 'methodology_pending';
  const sdiNotComputable =
    sdiFinancialStrength && sdiAssessmentState === 'not_computable';
  const sdiReason =
    typeof liveRating?.metrics.reason === 'string' ? liveRating.metrics.reason : undefined;


  const referenceRates =
    data?.indices.filter((index) => isReferenceRateCode(index.indexCode)) ?? [];
  const otherIndices =
    data?.indices.filter((index) => !isReferenceRateCode(index.indexCode)) ?? [];
  const overlayCurve =
    data?.curves.find((curve) => curve.curveName === overlayCurveName) ?? null;

  const isEmpty =
    data !== undefined &&
    data.curves.length === 0 &&
    data.fxRates.length === 0 &&
    data.ratings.length === 0 &&
    data.indices.length === 0 &&
    !liveRating;

  const emptyState = (
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
  );

  function renderViewTab() {
    if (!data) return null;
    // The Forward tab reads a separate endpoint and carries its own curve
    // empty-state, so it is not blanked by the shared "no market data" guard.
    if (isEmpty && !(tab === 'market-data' && marketDataView === 'forward')) return emptyState;

    if (tab === 'overview') {
      return (
        <div className="space-y-6">
          {!isReproduction &&
          sdiFinancialStrength &&
          liveRating &&
          liveRating.metrics.availability !== 'unavailable' &&
          ratingLadder.data?.snapshots?.length ? (
            // SDI with an assessed history: the current assessment and how it
            // got here sit side by side — one read, no scrolling between them.
            // Agency observations (a thin strip, often empty for an SDI) moves
            // to a full-width row beneath instead of holding a whole column.
            <div className="space-y-5">
              <Section
                title="Credit monitor"
                subtitle="Live internal assessment derived from Treasury and ALM inputs"
              >
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-stretch">
                  <SdiFinancialStrengthCard rating={liveRating} />
                  <SdiFinancialStrengthTrend
                    snapshots={ratingLadder.data.snapshots}
                    className="h-full"
                  />
                </div>
              </Section>
              <Section
                title="Agency observations"
                subtitle="Market ratings used to frame the sovereign and counterparty context"
              >
                {data.ratings.length > 0 ? (
                  <RatingsStrip ratings={data.ratings} />
                ) : (
                  <div className="border border-border bg-surface-raised px-5 py-4 text-caption text-slate rounded-lg">
                    No agency observations are available on the selected source.
                  </div>
                )}
              </Section>
            </div>
          ) : null}

          {!isReproduction &&
          !(
            sdiFinancialStrength &&
            liveRating &&
            liveRating.metrics.availability !== 'unavailable' &&
            ratingLadder.data?.snapshots?.length
          ) && (
            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.85fr)] gap-5 items-start">
              <Section
                title="Credit monitor"
                subtitle="Live internal assessment derived from Treasury and ALM inputs"
              >
                {liveRating &&
                liveRating.metrics.availability !== 'unavailable' ? (
                  // An SDI gets its OWN card: AEQ-GH-SDI-FS releases component
                  // scores, not the bank scorecard's grade / PD / sovereign
                  // ceiling, so ImpliedRatingCard would render an empty grade
                  // block and read as a broken rating. (An SDI WITH an assessed
                  // trend renders in the side-by-side block above; this branch
                  // is the no-history-yet case.)
                  sdiFinancialStrength ? (
                    <SdiFinancialStrengthCard rating={liveRating} />
                  ) : (
                    <ImpliedRatingCard rating={liveRating} />
                  )
                ) : (
                  <div className="border border-border bg-surface-raised px-5 py-4 text-body text-slate rounded-lg">
                    <p>
                      {liveRating?.pipelineState === 'failed'
                        ? 'The prior live assessment is no longer current.'
                        : sdiMethodologyPending
                          ? 'SDI financial-strength methodology pending approval.'
                        : sdiNotComputable
                          ? 'SDI financial-strength assessment not computable at this date.'
                        : sdiFinancialStrength
                          ? 'SDI financial-strength assessment unavailable.'
                        : 'No live assessment is available yet.'}
                    </p>
                    {sdiMethodologyPending ? (
                      <p className="mt-2 text-caption text-navy/85 leading-relaxed">
                        No credit grade or probability of default is issued until the
                        `AEQ-GH-SDI-FS` methodology is calibrated, independently validated, and
                        approved.
                      </p>
                    ) : sdiNotComputable ? null : sdiFinancialStrength ? (
                      <p className="mt-2 text-caption text-navy/85 leading-relaxed">
                        {sdiReason ??
                          'The SDI financial-strength assessment is unavailable at this date.'}
                      </p>
                    ) : liveRating?.pipelineState === 'failed' ? (
                      <p className="mt-2 text-caption text-navy/85 leading-relaxed">
                        A current canonical financial book is required before the assessment can
                        be recomputed. The live pipeline retries automatically at the latest
                        available reporting date.
                      </p>
                    ) : typeof liveRating?.metrics.reason === 'string' &&
                    liveRating.metrics.reason ? (
                      <p className="mt-2 text-caption text-navy/85 leading-relaxed">
                        Missing prerequisite: {liveRating.metrics.reason}
                      </p>
                    ) : (
                      <p className="mt-2 text-caption text-slate leading-relaxed">
                        A canonical-data refresh will calculate the assessment once its required
                        market and live-engine inputs are available.
                      </p>
                    )}
                    {liveRating && (
                      <p className="mt-2 text-caption text-slate">
                        {liveRating.pipelineState === 'failed'
                          ? `Last calculation failed ${fmtTimestamp(liveRating.computedAt)}.`
                          : `Last calculation ${fmtTimestamp(liveRating.computedAt)}.`}{' '}
                        Source data as of {fmtDateUTC(liveRating.sourceAsOfDate)}.
                      </p>
                    )}
                    {liveRating?.pipelineError && (
                      <p className="mt-1 text-caption text-critical">
                        {liveRating.pipelineError}
                      </p>
                    )}
                  </div>
                )}
              </Section>

              <Section
                title="Agency observations"
                subtitle="Market ratings used to frame the sovereign and counterparty context"
              >
                {data.ratings.length > 0 ? (
                  <RatingsStrip ratings={data.ratings} />
                ) : (
                  <div className="border border-border bg-surface-raised px-5 py-4 text-caption text-slate rounded-lg">
                    No agency observations are available on the selected source.
                  </div>
                )}
              </Section>
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-y border-border-light py-3">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">
              Engine feed
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <SourceIndicator
                category="curves"
                preference={prefs.data?.curves}
                onManage={() => setTab('sources')}
              />
              <SourceIndicator
                category="fx"
                preference={prefs.data?.fx}
                onManage={() => setTab('sources')}
              />
              <SourceIndicator
                category="rates"
                preference={prefs.data?.rates}
                onManage={() => setTab('sources')}
              />
            </div>
          </div>

          <div className="space-y-5">
            {/* Rates and curves sit side by side — the two feeds a treasurer scans
                together. On narrow viewports they stack. */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
              {referenceRates.length > 0 && (
                <Section
                  title="Rate monitor"
                  subtitle="Policy, reference, and lending rates on the selected source"
                >
                  <RatesBoard indices={referenceRates} groups={['policy', 'lending']} />
                </Section>
              )}

              {data.fxRates.length > 0 && (
                <Section
                  title="FX monitor"
                  subtitle="Spot per pair, day movement, and persisted quote history"
                >
                  <FxBoard fxRates={data.fxRates} />
                </Section>
              )}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
              {referenceRates.length > 0 && (
                <Section
                  title="Money market"
                  subtitle="Interbank and bill auction rates on the selected source"
                >
                  <RatesBoard indices={referenceRates} groups={['money-market']} />
                </Section>
              )}

              {data.curves.length > 0 && (
                <Section
                  title="Curve monitor"
                  subtitle="Published discount, zero, and forward curves. Select a row for the curve workbench."
                >
                  <CurveThumbnails
                    curves={data.curves}
                    onOpen={(curveName) => {
                      setSelectedCurveName(curveName);
                      setTab('curves');
                    }}
                  />
                </Section>
              )}
            </div>

            {otherIndices.length > 0 && (
              <Section title="Indicators" subtitle="Macro indices and forecasts by scenario">
                <IndicesStrip indices={otherIndices} />
              </Section>
            )}
          </div>
        </div>
      );
    }

    if (tab === 'curves') {
      if (data.curves.length === 0) {
        return (
          <EmptyState
            Icon={CandlestickChart}
            title="No curves published"
            description="The canonical store has no servable curves for this bank at the as-of date."
          />
        );
      }
      return (
        <div className="space-y-8">
          <div className="flex justify-end">
            <SourceIndicator
              category="curves"
              preference={prefs.data?.curves}
              onManage={() => setTab('sources')}
            />
          </div>
          <Section
            title="Curves explorer"
            subtitle="Pick a published curve, reproduce it at any past as-of date, read the tenor-adjusted forward grid, and layer your private spreads"
          >
            <CurvesExplorer
              curves={data.curves}
              asOfDate={data.asOfDate}
              isReproduction={isReproduction}
              selectedCurveName={selectedCurveName}
              onSelectCurve={setSelectedCurveName}
              onOpenForward={(curveName) => {
                setSelectedCurveName(curveName);
                setMarketDataView('forward');
                setTab('market-data');
              }}
              onEditOverlays={(curveName) => setOverlayCurveName(curveName)}
            />
          </Section>
          <Section
            title="Curve board"
            subtitle="Every published curve at the as-of date — official base vs your private spread composition"
          >
            <CurveBoard
              curves={data.curves}
              onEditOverlays={(curveName) => setOverlayCurveName(curveName)}
            />
          </Section>
        </div>
      );
    }

    if (tab === 'market-data' && marketDataView === 'forward') {
      if (!bank) return null;
      return (
        <ForwardTab
          bankId={bank.id}
          curves={data.curves}
          asOf={asOf}
          selectedCurveName={selectedCurveName}
          onSelectCurve={setSelectedCurveName}
        />
      );
    }

    if (tab === 'market-data' && marketDataView === 'fx') {
      if (data.fxRates.length === 0 && data.fxForwards.length === 0) {
        return (
          <EmptyState
            Icon={CandlestickChart}
            title="No FX rates published"
            description="The canonical store has no servable FX spot rates for this bank at the as-of date."
          />
        );
      }
      return (
        <div className="space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light pb-4">
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate">Foreign exchange</p>
              <h2 className="mt-1 text-h2 text-navy">FX market monitor</h2>
              <p className="mt-1 text-caption text-slate">Historical spot observations and market-implied forward forecasts from the selected source.</p>
            </div>
            <SourceIndicator
              category="fx"
              preference={prefs.data?.fx}
              onManage={() => setTab('sources')}
            />
          </div>
          {data.fxRates.length > 0 && (
            <Section title="Historical spot" subtitle="Arbitrated spot observations, one-day movement, and persisted quote history.">
              <FxBoard fxRates={data.fxRates} />
            </Section>
          )}
          <Section
            title="Market-implied FX forward forecasts"
            subtitle="Forward outrights supplied by the selected canonical source; they are market-implied, not a statistical prediction."
          >
            <FxForwardsBoard forwards={data.fxForwards} spots={data.fxRates} />
          </Section>
        </div>
      );
    }

    // rates — the remaining Market data sub-view.
    if (referenceRates.length === 0 && otherIndices.length === 0) {
      return (
        <EmptyState
          Icon={CandlestickChart}
          title="No reference rates published"
          description="The canonical store has no servable reference rates or indices for this bank at the as-of date."
        />
      );
    }
    return (
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light pb-4">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Reference data</p>
            <h2 className="mt-1 text-h2 text-navy">Rates monitor</h2>
            <p className="mt-1 text-caption text-slate">Policy, money-market, lending, and macro inputs resolved on the selected source plane.</p>
          </div>
          <SourceIndicator
            category="rates"
            preference={prefs.data?.rates}
            onManage={() => setTab('sources')}
          />
        </div>
        <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.85fr)] items-start">
          {referenceRates.length > 0 && (
            <Section
              title="Reference rates"
              subtitle="Policy, money-market, and lending reference rates"
            >
              <RatesBoard indices={referenceRates} />
            </Section>
          )}
          {otherIndices.length > 0 && (
            <Section title="Indicators" subtitle="Scenario-tagged macro inputs and forecasts">
              <IndicesStrip indices={otherIndices} />
            </Section>
          )}
        </div>
        {data.curves.some((curve) => curve.curveType === 'forward') && (
          <Section
            title="Published forward-rate forecasts"
            subtitle="Approved desk forward curves are the term structure of expected market rates. Open one to inspect every published tenor."
          >
            <CurveThumbnails
              curves={data.curves.filter((curve) => curve.curveType === 'forward')}
              onOpen={(curveName) => {
                setSelectedCurveName(curveName);
                setMarketDataView('forward');
              }}
            />
          </Section>
        )}
      </div>
    );
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Markets' }]}
        title="Markets"
        subtitle="Live market monitor for curves, rates, FX, and credit inputs feeding Treasury and risk engines."
        action={
          <div className="flex items-center gap-4">
            <AsOfControl asOf={asOf} todayIso={todayIso} onChange={setAsOf} />
            <ManageSourcesLink />
          </div>
        }
      />

      <SdiModuleContext title="SDI ALM context">
        Market curves and government-security reference data support valuation and proportionate balance-sheet management. FX views are relevant only where the institution has a material foreign-currency book.
      </SdiModuleContext>

      <div className="px-8 py-6 space-y-6">
        <SubTabs items={TABS} active={tab} onChange={(key) => setTab(key as TabKey)} />

        {tab === 'market-data' && (
          <SubTabs
            items={MARKET_DATA_TABS}
            active={marketDataView}
            onChange={(key) => setMarketDataView(key as MarketDataView)}
          />
        )}

        {isReproduction && tab !== 'sources' && data && (
          <ReproductionBanner asOfDate={data.asOfDate} />
        )}

        {tab === 'sources' ? (
          bank ? (
            <SourcesControlRoom bankId={bank.id} asOf={asOf} />
          ) : null
        ) : (
          <QueryBoundary
            isLoading={views.isLoading}
            error={views.error}
            onRetry={() => views.refetch()}
          >
            {renderViewTab()}
          </QueryBoundary>
        )}
      </div>

      {bank && overlayCurve && (
        <OverlayDrawer
          bankId={bank.id}
          bankName={bank.name}
          curve={overlayCurve}
          onClose={() => setOverlayCurveName(null)}
        />
      )}
    </>
  );
}
