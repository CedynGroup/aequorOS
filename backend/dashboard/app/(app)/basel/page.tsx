'use client';

import PageHeader from '@/components/ui/PageHeader';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import Sparkline from '@/components/ui/Sparkline';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DonutChart from '@/components/charts/DonutChart';
import RatioTrendChart from '@/components/liquidity/charts/RatioTrendChart';
import CapitalWaterfallChart from '@/components/basel/charts/CapitalWaterfallChart';
import SdiCapitalView from '@/components/basel/SdiCapitalView';
import FloorNotAssessed from '@/components/basel/FloorNotAssessed';
import { runComputedAt } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import LiveEngineNote from '@/components/live/LiveEngineNote';
import {
  useCapitalDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import {
  assessAgainstFloor,
  floorStatus,
  fmtFloorPct,
  num,
  numOrNull,
  statusTone,
  type FloorAssessment,
} from '@/lib/api/values';
import { seriesColor } from '@/lib/chartTheme';
import { fmtCurrency, fmtPct, regShort } from '@/lib/format';

function kpiStatus(status: 'green' | 'amber' | 'red' | string): KpiStatus {
  return status === 'red' ? 'crit' : status === 'amber' ? 'warn' : 'ok';
}

/**
 * The engine's traffic light, but only where the comparison it encodes can
 * actually be made. When the governed floor (or the ratio) did not resolve the
 * KPI takes `floorStatus`, which is never `ok` — a green edge is a compliance
 * affirmation and must not be drawn on an assessment nobody made.
 */
function gatedKpiStatus(
  assessment: FloorAssessment,
  engineStatus: string
): KpiStatus {
  return assessment.assessed ? kpiStatus(engineStatus) : floorStatus(assessment);
}

/** The KPI caption under a ratio: the resolved floor, or its stated absence. */
function floorHint(floor: number | null, absence: string): string {
  return floor === null ? absence : `Regulatory minimum ${fmtFloorPct(floor)}`;
}

/**
 * The rule codes whose verdict is a comparison against a governed capital
 * floor, mapped to the floor each one uses. The Validations card re-reads this
 * so a stored run's PASS cannot outlive the requirement it was measured
 * against: a run records what was applied WHEN IT RAN, and if that floor no
 * longer resolves from the institution's parameter set the row is restated as
 * "not assessed" rather than presented as today's verdict — the same authority,
 * and therefore the same answer, as the KPI edge and the floors panel.
 */
const FLOOR_RULE_CODES = {
  car_above_minimum: 'capital adequacy',
  cet1_above_minimum: 'CET1',
  tier1_above_minimum: 'Tier 1',
  leverage_above_minimum: 'leverage-ratio',
} as const;

export default function BaselOverview() {
  const { bank, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const institutionClass = moduleScope.institutionClass;
  const isScopeResolved = moduleScope.isResolved;
  const shouldLoadBaselDashboard = isScopeResolved && institutionClass !== 'sdi';

  const dashboard = useCapitalDashboard(shouldLoadBaselDashboard ? bankId : undefined);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;
  const run = latestRun.data;
  // NEW-51. The CAR buffer ladder is tenant data: the capital service resolves
  // `car_min`, `car_early_warning` and `car_critical` from the institution's
  // regulatory parameter set and this endpoint refuses with 409
  // `missing_parameter` rather than guessing when any of them is unset. A
  // literal here is therefore never a "sensible default" — it is a fabricated
  // regulatory floor that every ratio on this page is then judged against. The
  // Bank of Ghana minimum is CRD ¶71's 10% *plus* the ¶75 capital-conservation
  // buffer (13% today) and has moved four times since 2020, so a written-down
  // `10` silently understates the bar; `10.5` and `9` matched no published
  // instrument at all. Absence stays absence and every consumer below renders
  // it as "not assessed" — never green, never a breach against an invented bar.
  const carMin = numOrNull(data?.buffers.carMinPct);
  const carEarlyWarning = numOrNull(data?.buffers.carEarlyWarningPct);
  const carCritical = numOrNull(data?.buffers.carCriticalPct);
  // NEW-53. A FLOOR HAS ONE AUTHORITY, AND EVERY PANEL READS IT.
  //
  // These four minima all come from `buffers` — the institution's active
  // regulatory parameter set, resolved server-side by
  // `regulatory_capital._buffers_or_409` from the same dict the engine is
  // handed. That is what makes the three panels below agree: the KPI edge (the
  // engine's traffic light, classified against these floors), this floors
  // panel, and the validation rules (evaluated against these floors) are three
  // views of one comparison.
  //
  // Tier 1 / CET1 / leverage USED to read `runMetricThreshold(run, …)` — the
  // threshold snapshotted into the latest stored RegulatoryRun. That is a
  // historical record of what was applied when that run executed, not the
  // current requirement, and `latestRunId` is null for every bank before its
  // first official capital run. So the page showed a green Tier 1 KPI, "This
  // run carries no Tier 1 minimum · NOT ASSESSED", and a passing Tier 1
  // validation citing 8%, simultaneously. The absence of a run is not evidence
  // of compliance and it is not evidence of a missing floor.
  const tier1Min = numOrNull(data?.buffers.tier1MinPct);
  const cet1Min = numOrNull(data?.buffers.cet1MinPct);
  const leverageMin = numOrNull(data?.buffers.leverageMinPct);
  const carAssessment = assessAgainstFloor(
    numOrNull(data?.metrics.carPct),
    carMin
  );
  const tier1Assessment = assessAgainstFloor(
    numOrNull(data?.metrics.tier1RatioPct),
    tier1Min
  );
  const cet1Assessment = assessAgainstFloor(
    numOrNull(data?.metrics.cet1RatioPct),
    cet1Min
  );
  const leverageAssessment = assessAgainstFloor(
    numOrNull(data?.metrics.leverageRatioPct),
    leverageMin
  );
  const floorByRuleCode: Record<string, number | null> = {
    car_above_minimum: carMin,
    cet1_above_minimum: cet1Min,
    tier1_above_minimum: tier1Min,
    leverage_above_minimum: leverageMin,
  };

  const totalRwa = num(data?.metrics.totalRwaGhs);
  const rwaSlices = data
    ? [
        {
          name: 'Credit risk',
          value: num(data.rwaComposition.creditRwaGhs),
          color: seriesColor(0),
        },
        {
          name: 'Operational risk',
          value: num(data.rwaComposition.operationalRwaGhs),
          color: seriesColor(1),
        },
        {
          name: 'Market risk',
          value: num(data.rwaComposition.marketRwaGhs),
          color: seriesColor(2),
        },
      ]
    : [];

  const carTrend = (data?.trend ?? []).map((p) => num(p.carPct));
  const tier1Trend = (data?.trend ?? []).map((p) => num(p.tier1RatioPct));
  const cet1Trend = (data?.trend ?? []).map((p) => num(p.cet1RatioPct));
  const periodDelta = (series: number[]): number | undefined =>
    series.length >= 2
      ? series[series.length - 1] - series[series.length - 2]
      : undefined;
  const carDelta = periodDelta(carTrend);
  const tier1Delta = periodDelta(tier1Trend);
  const cet1Delta = periodDelta(cet1Trend);
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);
  // "Compliant n of m" is a compliance claim, so it exists only when there is a
  // floor to have complied with.
  const compliantCount =
    carMin === null ? null : carTrend.filter((v) => v >= carMin).length;
  // The trend chart's y-floor anchors on the critical buffer when there is one;
  // an unresolved buffer just means the axis is driven by the data alone.
  const carTrendAnchors = [
    ...carTrend,
    ...(carCritical === null ? [] : [carCritical]),
  ];
  const carTrendYMin =
    carTrendAnchors.length > 0
      ? Math.floor(Math.min(...carTrendAnchors) - 2)
      : undefined;

  const structure = data?.capitalStructure;
  const cet1Gross = structure
    ? structure.cet1Components.reduce((s, c) => s + num(c.weightedAmount), 0)
    : 0;
  const deductions = structure
    ? structure.cet1Deductions.reduce(
        (s, c) => s + Math.abs(num(c.weightedAmount)),
        0
      )
    : 0;

  // The third panel of the three. A validation is a rule evaluated against a
  // floor; when that floor no longer resolves from the institution's parameter
  // set the rule is not evaluable now, whatever a stored run once recorded. The
  // row is restated as "not assessed" so the Validations card cannot claim a
  // pass the KPI edge and the floors panel are refusing to claim.
  const validations = (data?.validations ?? []).map((item) => {
    const what = FLOOR_RULE_CODES[item.ruleCode as keyof typeof FLOOR_RULE_CODES];
    if (what === undefined || floorByRuleCode[item.ruleCode] !== null) {
      return item;
    }
    return {
      ...item,
      assessed: false,
      message:
        `No ${what} minimum resolves from this institution's active parameter ` +
        'set, so this rule cannot be evaluated for the current period.',
    };
  });

  const computedAt = runComputedAt(run);
  const provenance = data ? (
    <span>
      Computed from current positions and the active parameter set
    </span>
  ) : undefined;

  // Avoid showing or fetching the bank Basel experience while the bank payload
  // has not yet resolved its institution class.
  if (!isScopeResolved) {
    return <CapitalScopeLoading />;
  }

  // An SDI sees the simplified s.29 capital view, not the Basel 3-tier overview
  // (docs/sdi.md §4.2). The hooks above are inert for this branch.
  if (institutionClass === 'sdi') {
    return <SdiCapitalView bankId={bankId} />;
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital' },
          { label: 'Overview' },
        ]}
        title="Basel Capital"
        subtitle={`Capital Adequacy Ratio · Tier 1 / Tier 2 · ${regShort()} CRD framework`}
        action={data ? <LiveEngineNote live={data.live} stored={data.stored} /> : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">

            {/* Headline ratios */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiStat
                label="Capital Adequacy Ratio"
                value={num(data.metrics.carPct).toFixed(2)}
                unit="%"
                status={gatedKpiStatus(carAssessment, data.metrics.carStatus)}
                delta={carDelta}
                sparkline={<Sparkline data={carTrend} />}
                hint={
                  carMin === null
                    ? 'No capital adequacy minimum on file — compliance not assessed'
                    : `${regShort()} minimum ${fmtFloorPct(carMin)}`
                }
              />
              <KpiStat
                label="Tier 1 ratio"
                value={num(data.metrics.tier1RatioPct).toFixed(2)}
                unit="%"
                status={gatedKpiStatus(tier1Assessment, data.metrics.tier1Status)}
                delta={tier1Delta}
                sparkline={<Sparkline data={tier1Trend} />}
                hint={floorHint(
                  tier1Min,
                  'No Tier 1 minimum on file — compliance not assessed'
                )}
              />
              <KpiStat
                label="CET1 ratio"
                value={num(data.metrics.cet1RatioPct).toFixed(2)}
                unit="%"
                status={gatedKpiStatus(cet1Assessment, data.metrics.cet1Status)}
                delta={cet1Delta}
                sparkline={<Sparkline data={cet1Trend} />}
                hint={floorHint(
                  cet1Min,
                  'No CET1 minimum on file — compliance not assessed'
                )}
              />
              <KpiStat
                label="Leverage ratio"
                value={num(data.metrics.leverageRatioPct).toFixed(2)}
                unit="%"
                status={gatedKpiStatus(
                  leverageAssessment,
                  data.metrics.leverageStatus
                )}
                hint={floorHint(
                  leverageMin,
                  'No leverage-ratio minimum on file — compliance not assessed'
                )}
              />
            </div>

            {/* Regulatory floors — CAR & companions are floor limits */}
            <SectionCard
              title="Regulatory floors"
              // Deliberately NOT "`regShort()` CRD minimums". Only `car_min` is
              // clamped against a governed control-plane row, so only the CAR
              // row below may carry a regulator's name. CET1 / Tier 1 /
              // leverage arrive from the institution's own board register
              // unclamped (the control plane seeds no row for them —
              // `15_known_limitations.md` §3.5.1), and the fixture's leverage
              // 3% is Basel III's figure against BoG CRD ¶90's 6%. Naming the
              // regulator over a number it did not set is the attribution
              // defect this programme already corrected on the liquidity
              // screens.
              subtitle="Each ratio against the minimum resolved from this institution's parameter set — compliant while it stays above its floor"
              computedAt={computedAt}
              footer={provenance}
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
                {carMin === null ? (
                  <FloorNotAssessed
                    label="CAR"
                    value={numOrNull(data.metrics.carPct)}
                    reason="No capital adequacy minimum on file for this institution"
                  />
                ) : (
                  <LimitBar
                    label="CAR"
                    value={num(data.metrics.carPct)}
                    limit={carMin}
                    warnAt={carEarlyWarning ?? undefined}
                    direction="above"
                    unit="%"
                    limitLabel={`${regShort()} minimum`}
                    warnLabel={
                      data.buffers.carEarlyWarningLabel || 'Early warning'
                    }
                    format={(v) => v.toFixed(1)}
                  />
                )}
                {/* An "assumed minimum" is still an invented one: it places the
                    breach zone, colours the bar and prints a headroom figure
                    against a number no regulator set and no tenant configured.
                    A ratio whose governed floor does not resolve gets the
                    measurement and an explicit non-assessment instead — and,
                    per NEW-53, the KPI edge above and the validation row below
                    reach the same verdict from the same authority. */}
                {tier1Min === null ? (
                  <FloorNotAssessed
                    label="Tier 1 ratio"
                    value={numOrNull(data.metrics.tier1RatioPct)}
                    reason="No Tier 1 minimum on file for this institution"
                  />
                ) : (
                  <LimitBar
                    label="Tier 1 ratio"
                    value={num(data.metrics.tier1RatioPct)}
                    limit={tier1Min}
                    warnAt={tier1Min}
                    direction="above"
                    unit="%"
                    limitLabel="Regulatory minimum"
                    format={(v) => v.toFixed(1)}
                  />
                )}
                {cet1Min === null ? (
                  <FloorNotAssessed
                    label="CET1 ratio"
                    value={numOrNull(data.metrics.cet1RatioPct)}
                    reason="No CET1 minimum on file for this institution"
                  />
                ) : (
                  <LimitBar
                    label="CET1 ratio"
                    value={num(data.metrics.cet1RatioPct)}
                    limit={cet1Min}
                    warnAt={cet1Min}
                    direction="above"
                    unit="%"
                    limitLabel="Regulatory minimum"
                    format={(v) => v.toFixed(1)}
                  />
                )}
                {leverageMin === null ? (
                  <FloorNotAssessed
                    label="Leverage ratio"
                    value={numOrNull(data.metrics.leverageRatioPct)}
                    reason="No leverage-ratio minimum on file for this institution"
                  />
                ) : (
                  <LimitBar
                    label="Leverage ratio"
                    value={num(data.metrics.leverageRatioPct)}
                    limit={leverageMin}
                    warnAt={leverageMin}
                    direction="above"
                    unit="%"
                    limitLabel="Regulatory minimum"
                    format={(v) => v.toFixed(1)}
                  />
                )}
              </div>
            </SectionCard>

            {/* Trend + RWA composition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChartFrame
                className="lg:col-span-2"
                title="CAR — reporting-period trend"
                subtitle={`CAR and Tier 1 across ${carTrend.length} reporting periods`}
                height={260}
                actions={
                  compliantCount === null ? (
                    <StatusPill tone="pending">
                      No CAR minimum on file — compliance not assessed
                    </StatusPill>
                  ) : (
                    <StatusPill tone="success">
                      Compliant {compliantCount} of {carTrend.length}
                    </StatusPill>
                  )
                }
                footer={
                  <>
                    {hasInlineTrendPoints ? (
                      <span>
                        Hollow points are live computations — they solidify once
                        those periods’ results are stored.
                      </span>
                    ) : (
                      <span>All trend points come from stored results.</span>
                    )}
                    {carMin === null && (
                      <span>
                        {' '}
                        No capital adequacy minimum resolved for this
                        institution, so no floor line is drawn.
                      </span>
                    )}
                  </>
                }
              >
                <RatioTrendChart
                  data={(data.trend ?? []).map((p) => ({
                    label: p.label,
                    primary: num(p.carPct),
                    secondary: num(p.tier1RatioPct),
                    stored: p.stored,
                  }))}
                  threshold={carMin}
                  thresholdLabel={`${regShort()} min`}
                  redFloor={carEarlyWarning}
                  redFloorLabel="Early warning"
                  primaryLabel="CAR"
                  secondaryLabel="Tier 1"
                  yMin={carTrendYMin}
                  height={260}
                />
              </ChartFrame>

              <SectionCard
                title="RWA composition"
                subtitle={`Total ${fmtCurrency(totalRwa)}`}
              >
                <div className="space-y-4">
                  <DonutChart
                    data={rwaSlices}
                    centerLabel="Total RWA"
                    centerValue={fmtCurrency(totalRwa)}
                    format="ccy-m"
                  />
                  <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                    {rwaSlices.map((s) => (
                      <li key={s.name} className="flex items-center gap-3">
                        <span
                          className="w-2 h-2 rounded-sm shrink-0"
                          style={{ background: s.color }}
                          aria-hidden
                        />
                        <span className="text-navy/85 flex-1">{s.name}</span>
                        <span className="font-mono text-navy tnum">
                          {fmtCurrency(s.value)}
                        </span>
                        <span className="font-mono text-slate w-12 text-right tnum">
                          {totalRwa > 0
                            ? `${((s.value / totalRwa) * 100).toFixed(1)}%`
                            : '—'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </SectionCard>
            </div>

            {/* Capital waterfall */}
            {structure && (
              <ChartFrame
                title="Capital waterfall"
                subtitle="CET1 components → deductions → AT1 → Tier 2 → total qualifying capital"
                height={280}
                footer={
                  <span>
                    CET1 {fmtCurrency(num(structure.cet1CapitalGhs))} ·
                    Tier 1 {fmtCurrency(num(structure.tier1CapitalGhs))} ·
                    Total {fmtCurrency(num(structure.totalCapitalGhs))} ·{' '}
                    {fmtPct(num(data.metrics.carPct), 2)} of RWA
                  </span>
                }
              >
                <CapitalWaterfallChart
                  cet1Gross={cet1Gross}
                  deductions={deductions}
                  at1={num(structure.at1CapitalGhs)}
                  tier2={num(structure.tier2CapitalGhs)}
                  total={num(structure.totalCapitalGhs)}
                  height={280}
                />
              </ChartFrame>
            )}

            {/* Regulatory buffers */}
            <SectionCard
              title="Regulatory buffer status"
              subtitle={`${regShort()} CRD thresholds for the Capital Adequacy Ratio`}
              computedAt={computedAt}
              footer={provenance}
            >
              <div className="grid grid-cols-2 md:grid-cols-5 gap-5">
                <BufferCell
                  label={`${regShort()} minimum CAR`}
                  value={carMin}
                  note="Hard regulatory floor"
                />
                <BufferCell
                  label="Early warning"
                  value={carEarlyWarning}
                  note={data.buffers.carEarlyWarningLabel}
                />
                <BufferCell
                  label="Critical floor"
                  value={carCritical}
                  note="Supervisory intervention level"
                />
                <BufferCell
                  label="Current CAR"
                  value={numOrNull(data.buffers.currentCarPct)}
                  note="As of this reporting period"
                  emphasis={
                    carMin === null
                      ? undefined
                      : statusTone(data.metrics.carStatus)
                  }
                />
                <BufferCell
                  label="Headroom"
                  // Headroom is distance to the minimum. With no minimum there
                  // is no distance to state — not a zero one.
                  value={carMin === null ? null : numOrNull(data.buffers.headroomPp)}
                  suffix=" pp"
                  note={
                    carMin === null
                      ? 'No minimum resolved to measure headroom against'
                      : `Above the ${regShort()} minimum`
                  }
                  emphasis={
                    carMin === null
                      ? undefined
                      : statusTone(data.metrics.carStatus)
                  }
                />
              </div>
            </SectionCard>

            {/* Validations */}
            <SectionCard
              title="Validations"
              subtitle="Regulatory rule evaluation for this period"
              noPadding
              computedAt={computedAt}
              footer={provenance}
            >
              <ValidationList validations={validations} />
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

function CapitalScopeLoading() {
  return (
    <div className="px-8 py-6" aria-busy="true" aria-label="Loading regulatory capital scope">
      <div className="h-7 w-56 animate-pulse rounded bg-surface-hover" />
      <div className="mt-3 h-4 w-96 max-w-full animate-pulse rounded bg-surface-hover" />
    </div>
  );
}

function BufferCell({
  label,
  value,
  suffix = '%',
  note,
  emphasis,
}: {
  label: string;
  /**
   * The threshold or measurement. `null` is a first-class state: the ladder
   * prints "Not resolved" rather than a stand-in, because a plausible number in
   * this cell is read as the bar the institution is held to.
   */
  value: number | null;
  suffix?: string;
  note?: string;
  emphasis?: string;
}) {
  const valueColor =
    emphasis === 'breach' || emphasis === 'critical'
      ? 'text-critical'
      : emphasis === 'approaching' || emphasis === 'amber'
      ? 'text-warning'
      : emphasis
      ? 'text-success'
      : 'text-navy';
  return (
    <div className="space-y-1">
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p
        className={`font-mono tnum ${
          value === null ? 'text-body text-slate' : `text-h1 ${valueColor}`
        }`}
      >
        {value === null ? 'Not resolved' : `${value.toFixed(2)}${suffix}`}
      </p>
      {note && <p className="text-caption text-slate leading-snug">{note}</p>}
    </div>
  );
}
