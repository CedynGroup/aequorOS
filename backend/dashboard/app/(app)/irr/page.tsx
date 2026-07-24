'use client';

/**
 * IRRBB Overview: headline KPIs (worst ΔEVE/Tier 1, duration gap, NII
 * sensitivity, live status), ΔEVE tornado, repricing mini-ladder, trend, and
 * the validation panel. Every figure is a backend engine output.
 */

import Link from 'next/link';
import { ArrowUpRight, Zap } from 'lucide-react';
import IrrWorkspace from '@/components/irr/IrrWorkspace';
import TornadoChart from '@/components/irr/charts/TornadoChart';
import RepricingLadderChart from '@/components/irr/charts/RepricingLadderChart';
import TrendChart from '@/components/irr/charts/TrendChart';
import { scenarioLabel } from '@/components/irr/scenarios';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import RunBadge from '@/components/ui/RunBadge';
import Sparkline from '@/components/ui/Sparkline';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import ValidationList from '@/components/ui/ValidationList';
import { fmtRelative, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct, regShort } from '@/lib/format';

function kpiStatus(status: string): KpiStatus | undefined {
  return status === 'green'
    ? 'ok'
    : status === 'amber'
    ? 'warn'
    : status === 'red'
    ? 'crit'
    : undefined;
}

const SPARK_COLOR: Record<KpiStatus, string> = {
  ok: 'rgb(var(--ok))',
  warn: 'rgb(var(--warn))',
  crit: 'rgb(var(--crit))',
};

export default function IrrOverviewPage() {
  return (
    <IrrWorkspace
      crumb="Overview"
      subtitle={`Banking book IRRBB · Repricing gap · EVE & EaR sensitivity · ${regShort()} CRD`}
    >
      {({ data, metrics: m, latestRun, computedAt }) => {
        const eveLimit = num(m.eveLimitPct);
        const worstPct = num(m.worstEveChangePctTier1);
        const worstStatus = kpiStatus(m.eveStatus) ?? 'ok';

        const earUp = num(m.earUp200Ghs);
        const earDown = num(m.earDown200Ghs);
        const earWorst = Math.min(earUp, earDown);

        const trend = data.trend ?? [];
        const worstSpark = trend.map((p) => num(p.worstEveChangePctTier1));
        const durationSpark = trend.map((p) => num(p.durationGap));
        const trendPoints = trend.map((p) => ({
          label: p.label,
          value: num(p.worstEveChangePctTier1),
          stored: p.stored,
        }));
        const hasInlineTrendPoints = trend.some((p) => !p.stored);

        const eveBars = (data.eveScenarios ?? [])
          .filter((s) => s.scenarioCode !== 'baseline')
          .map((s) => ({
            label: scenarioLabel(s.scenarioCode),
            value: num(s.deltaEveGhs),
            pctTier1: num(s.deltaEvePctTier1),
            breach: s.breach,
          }));

        const ladder = (data.gapTable ?? []).map((g) => ({
          bucket: g.bucket,
          rsa: num(g.rsaGhs),
          rsl: num(g.rslGhs),
          gap: num(g.gapGhs),
          cumulative: num(g.cumulativeGapGhs),
        }));

        const live = data.live ?? null;
        const runBadge = latestRun ? <RunBadge run={latestRun} /> : undefined;

        return (
          <>
            {/* KPI row */}
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              <KpiStat
                label="Worst ΔEVE / Tier 1"
                value={fmtPct(worstPct, 2)}
                status={worstStatus}
                hint={`${scenarioLabel(m.worstScenarioCode)} · limit ${eveLimit}%`}
                sparkline={
                  worstSpark.length > 1 ? (
                    <Sparkline
                      data={worstSpark}
                      color={SPARK_COLOR[worstStatus]}
                      width={56}
                    />
                  ) : undefined
                }
              />
              <KpiStat
                label="Duration gap"
                value={num(m.durationGap).toFixed(2)}
                unit="yrs"
                hint={`Assets ${num(m.assetDuration).toFixed(2)}y · Liabilities ${num(
                  m.liabilityDuration
                ).toFixed(2)}y`}
                sparkline={
                  durationSpark.length > 1 ? (
                    <Sparkline
                      data={durationSpark}
                      color="rgb(var(--accent))"
                      width={56}
                    />
                  ) : undefined
                }
              />
              <KpiStat
                label="NII sensitivity ±200bp"
                value={fmtCurrencySigned(earWorst)}
                status={earWorst < 0 ? 'warn' : 'ok'}
                hint={`+200bp ${fmtCurrencySigned(earUp)} · −200bp ${fmtCurrencySigned(
                  earDown,
                  'GHS'
                )}`}
              />
              <KpiStat
                label="Live engine"
                value={live ? live.status.toUpperCase() : data.stored ? 'STORED' : 'INLINE'}
                status={live ? kpiStatus(live.status) : undefined}
                hint={
                  live
                    ? `recomputed ${fmtRelative(live.computedAt)}`
                    : data.stored
                    ? 'from the latest official run'
                    : 'computed inline — not yet persisted'
                }
              />
            </div>

            {/* Tornado + mini ladder */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title="ΔEVE by scenario"
                subtitle={`Six Basel IRRBB shocks · Base EVE ${fmtCurrency(
                  num(m.eveBaseGhs),
                  'GHS'
                )} · Tier 1 ${fmtCurrency(num(m.tier1Ghs))}`}
                actions={
                  <Link
                    href="/irr/sensitivity"
                    className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
                  >
                    EVE & NII detail
                    <ArrowUpRight size={12} aria-hidden />
                  </Link>
                }
                computedAt={computedAt}
                runBadge={runBadge}
              >
                {eveBars.length > 0 ? (
                  <TornadoChart data={eveBars} height={280} />
                ) : (
                  <p className="text-body text-slate">No scenario results for this period.</p>
                )}
              </SectionCard>

              <SectionCard
                title="Repricing gap profile"
                subtitle={`Net gap per ${regShort()} CRD tenor bucket with cumulative overlay`}
                actions={
                  <Link
                    href="/irr/gaps"
                    className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
                  >
                    Full gap analysis
                    <ArrowUpRight size={12} aria-hidden />
                  </Link>
                }
                computedAt={computedAt}
                runBadge={runBadge}
                footer={
                  <span>
                    12-month cumulative gap{' '}
                    <span
                      className={`font-mono tnum font-medium ${
                        num(m.cumulative12mGapGhs) < 0 ? 'text-warning' : 'text-navy'
                      }`}
                    >
                      {fmtCurrencySigned(num(m.cumulative12mGapGhs))}
                    </span>
                  </span>
                }
              >
                {ladder.length > 0 ? (
                  <RepricingLadderChart data={ladder} height={280} mini />
                ) : (
                  <p className="text-body text-slate">No repricing buckets for this period.</p>
                )}
              </SectionCard>
            </div>

            {/* Trend */}
            <SectionCard
              title="Worst ΔEVE / Tier 1 — 12-period trend"
              subtitle="Trailing-year path of the worst-case EVE sensitivity"
              actions={
                <StatusPill tone={statusTone(m.eveStatus)}>
                  {fmtPct(worstPct, 2)} vs {eveLimit}% limit
                </StatusPill>
              }
              computedAt={computedAt}
              runBadge={runBadge}
              footer={
                hasInlineTrendPoints ? (
                  <span>
                    Hollow points are computed inline — run all scenarios on those
                    periods to persist them.
                  </span>
                ) : undefined
              }
            >
              {trendPoints.length > 0 ? (
                <TrendChart
                  data={trendPoints}
                  threshold={eveLimit}
                  thresholdLabel="Limit"
                  yMin={0}
                  label="Worst ΔEVE/Tier 1"
                />
              ) : (
                <EmptyState
                  Icon={Zap}
                  title="No trend history"
                  description="Run all scenarios to build the per-period EVE sensitivity trend."
                />
              )}
            </SectionCard>

            {/* Validations */}
            <SectionCard
              title="Validations"
              subtitle="IRRBB rule evaluation for this period"
              noPadding
              computedAt={computedAt}
              runBadge={runBadge}
            >
              <ValidationList validations={data.validations} />
            </SectionCard>
          </>
        );
      }}
    </IrrWorkspace>
  );
}
