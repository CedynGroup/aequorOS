'use client';

/**
 * Limits: bullet-gauge wall for the supervisory ΔEVE/Tier-1 limit — headline
 * worst-case plus every scenario — with headroom, threshold provenance, and
 * a breach history strip derived from the stored trend. The EaR limit is
 * expressed only through the engine's `ear_within_limit` validation (its
 * numeric threshold is not exposed on the dashboard payload, so no gauge is
 * fabricated for it).
 */

import IrrWorkspace from '@/components/irr/IrrWorkspace';
import IllustrativeBadge from '@/components/irr/IllustrativeBadge';
import { scenarioLabel } from '@/components/irr/scenarios';
import LimitBar from '@/components/ui/LimitBar';
import RunBadge from '@/components/ui/RunBadge';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import { fmtDateUTC, num, statusTone } from '@/lib/api/values';
import { fmtCurrencySigned, fmtPct } from '@/lib/format';

export default function IrrLimitsPage() {
  return (
    <IrrWorkspace
      crumb="Limits"
      subtitle="Supervisory limit utilisation, headroom and breach history"
    >
      {({ data, metrics: m, latestRun, computedAt }) => {
        const eveLimit = num(m.eveLimitPct);
        const worstPct = Math.abs(num(m.worstEveChangePctTier1));
        const runBadge = latestRun ? <RunBadge run={latestRun} /> : undefined;

        const shocks = (data.eveScenarios ?? []).filter(
          (s) => s.scenarioCode !== 'baseline'
        );

        const trend = data.trend ?? [];

        return (
          <>
            <SectionCard
              title="ΔEVE / Tier 1 — supervisory limit"
              subtitle={`Outlier threshold ${eveLimit}% of Tier 1 · source: BoG SDI supervisory parameter (eve_tier1_limit_pct)`}
              actions={
                <StatusPill tone={statusTone(m.eveStatus)}>
                  {fmtPct(worstPct, 2)} of {eveLimit}%
                </StatusPill>
              }
              computedAt={computedAt}
              runBadge={runBadge}
            >
              <LimitBar
                label={`Worst case — ${scenarioLabel(m.worstScenarioCode)}`}
                value={worstPct}
                limit={eveLimit}
                direction="below"
                unit="%"
                format={(v) => v.toFixed(2)}
                limitLabel="Supervisory limit"
                meta={
                  <span className="whitespace-nowrap">
                    ΔEVE{' '}
                    <span className="font-mono tnum font-medium text-navy">
                      {fmtCurrencySigned(num(m.worstEveChangeGhs), 'GHS')}
                    </span>
                  </span>
                }
              />
            </SectionCard>

            <SectionCard
              title="Limit utilisation by scenario"
              subtitle="Each Basel shock's |ΔEVE| as a share of Tier 1 against the same supervisory ceiling"
              computedAt={computedAt}
              runBadge={runBadge}
            >
              {shocks.length > 0 ? (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-10 gap-y-6">
                  {shocks.map((s) => {
                    const signed = num(s.deltaEvePctTier1);
                    return (
                      <LimitBar
                        key={s.scenarioCode}
                        label={scenarioLabel(s.scenarioCode)}
                        value={Math.abs(signed)}
                        limit={eveLimit}
                        max={Math.max(eveLimit * 1.3, worstPct * 1.15)}
                        direction="below"
                        unit="%"
                        format={(v) => v.toFixed(2)}
                        limitLabel="Limit"
                        meta={
                          <span className="whitespace-nowrap">
                            ΔEVE{' '}
                            <span
                              className={`font-mono tnum font-medium ${
                                num(s.deltaEveGhs) < 0 ? 'text-critical' : 'text-navy'
                              }`}
                            >
                              {fmtCurrencySigned(num(s.deltaEveGhs), 'GHS')}
                            </span>
                          </span>
                        }
                      />
                    );
                  })}
                </div>
              ) : (
                <p className="text-body text-slate">
                  No scenario results for this period — run all scenarios to
                  populate the limit wall.
                </p>
              )}
            </SectionCard>

            <SectionCard
              title="Breach history"
              subtitle="Stored per-period worst ΔEVE/Tier 1 from the trend, evaluated against the current limit"
              actions={
                <IllustrativeBadge
                  label="Derived"
                  title="Each period's stored worst ΔEVE/Tier 1 is compared with the limit configured today; historical limit values are not stored on the trend payload."
                />
              }
              computedAt={computedAt}
              runBadge={runBadge}
            >
              {trend.length > 0 ? (
                <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                  {trend.map((p) => {
                    const v = Math.abs(num(p.worstEveChangePctTier1));
                    const breached = v > eveLimit;
                    return (
                      <div
                        key={p.reportingPeriodId}
                        className={`rounded border px-2.5 py-2 ${
                          breached
                            ? 'border-critical/30 bg-critical-light'
                            : 'border-border-light bg-surface'
                        }`}
                        title={`${p.label} · period end ${fmtDateUTC(p.periodEnd)}${
                          p.stored ? '' : ' · computed inline'
                        }`}
                      >
                        <p className="text-micro uppercase tracking-wider text-slate truncate">
                          {p.label}
                          {!p.stored && <span aria-hidden> ◦</span>}
                        </p>
                        <p
                          className={`mt-0.5 font-mono tnum text-caption font-semibold ${
                            breached ? 'text-critical' : 'text-success'
                          }`}
                        >
                          {fmtPct(v, 2)}
                        </p>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-body text-slate">
                  No trend history yet — run all scenarios across periods to
                  build the breach record.
                </p>
              )}
              {trend.some((p) => !p.stored) && (
                <p className="mt-3 text-caption text-slate">
                  ◦ marks periods computed inline rather than from a stored run.
                </p>
              )}
            </SectionCard>

            <SectionCard
              title="Limit rule evaluations"
              subtitle="Engine validations for this period, including the earnings-at-risk limit check"
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
