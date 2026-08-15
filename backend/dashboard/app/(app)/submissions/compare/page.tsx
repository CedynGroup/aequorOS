'use client';

/**
 * Regulatory Reporting — Compare. A line-by-line diff of two generated returns,
 * with directional, favorability-coloured deltas that the SERVER computes (never
 * derived client-side — the diff an examiner reads has to be the one the platform
 * can stand behind, matching the History tab's version comparison precedent).
 *
 * Two modes:
 *   • Version — two run versions of the SAME reporting period + module.
 *   • Period  — one return (module + scenario) across TWO reporting periods.
 *
 * The selectors are fed from the shared regulatory-runs registry
 * (`useRegulatoryRuns`, filtered client-side by period/scenario) and the bank's
 * reporting-period list (`useReportingPeriods`); no dedicated list endpoint is
 * needed. Currency is jurisdiction-neutral via lib/format — never hardcoded.
 */

import { useEffect, useMemo, useState } from 'react';
import { ArrowLeftRight, GitCompareArrows, Scale } from 'lucide-react';
import type {
  BankReportingPeriodRead,
  RegulatoryRunSummaryRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { SemanticDelta } from '@/components/ui/DeltaBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRuns,
  useReportComparison,
  useReportingPeriods,
} from '@/lib/api/hooks';
import { isApiError } from '@/lib/api/client';
import { fmtDateUTC, fmtTimestamp, labelize } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtInt } from '@/lib/format';
import {
  COMPARISON_MODULES,
  COMPARISON_MODULE_LABELS,
  type ComparisonLine,
  type ComparisonMode,
  type ComparisonModule,
  type ComparisonSide,
  type ComparisonUnit,
} from '@/lib/api/reportComparison';

// ---------------------------------------------------------------------------
// Value / delta formatting — driven by the line's unit.
// ---------------------------------------------------------------------------

/** A cell value in its native unit: currency, percentage, ratio, or count. */
function formatValue(unit: ComparisonUnit, value: number | null): string {
  if (value === null) return '—';
  switch (unit) {
    case 'ccy':
      return fmtCurrency(value);
    case 'pct':
    case 'ratio':
      // Both are percentage-scaled on the wire (see reportComparison.ts).
      return `${value.toFixed(2)}%`;
    case 'count':
      return fmtInt(Math.round(value));
  }
}

/** The absolute delta (`delta_ccy`) in the line's native unit, signed. */
function formatAbsDelta(unit: ComparisonUnit, value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : '';
  switch (unit) {
    case 'ccy':
      return fmtCurrencySigned(value);
    case 'pct':
    case 'ratio':
      return `${sign}${value.toFixed(2)} pp`;
    case 'count':
      return `${sign}${fmtInt(Math.round(value))}`;
  }
}

/** The relative % change (`delta_pct`), signed — null/new handled by caller. */
function formatRelPct(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// Small building blocks.
// ---------------------------------------------------------------------------

function ModeToggle({
  mode,
  onChange,
}: {
  mode: ComparisonMode;
  onChange: (next: ComparisonMode) => void;
}) {
  const options: { value: ComparisonMode; label: string }[] = [
    { value: 'version', label: 'Version' },
    { value: 'period', label: 'Period' },
  ];
  return (
    <div
      role="tablist"
      aria-label="Comparison mode"
      className="inline-flex rounded-md border border-border bg-surface-raised p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === mode;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={`px-3 py-1 text-caption font-medium rounded transition-colors ${
              active
                ? 'bg-action-light text-action'
                : 'text-slate hover:text-navy'
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function Selector({
  label,
  value,
  onChange,
  disabled,
  children,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="text-micro font-medium text-slate uppercase tracking-wider">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy disabled:opacity-50 min-w-[12rem]"
      >
        {children}
      </select>
    </label>
  );
}

/** Favorable / adverse / neutral tally chips for the summary header. */
function CountChips({
  favorable,
  adverse,
  neutral,
}: {
  favorable: number;
  adverse: number;
  neutral: number;
}) {
  const chips = [
    { n: favorable, tone: 'text-success', label: 'favorable' },
    { n: adverse, tone: 'text-critical', label: 'adverse' },
    { n: neutral, tone: 'text-slate', label: 'neutral' },
  ];
  return (
    <div className="flex items-center gap-4">
      {chips.map((c) => (
        <div key={c.label} className="flex items-baseline gap-1.5">
          <span className={`text-h3 font-semibold tnum ${c.tone}`}>{c.n}</span>
          <span className="text-caption text-slate">{c.label}</span>
        </div>
      ))}
    </div>
  );
}

function SideCard({
  role,
  side,
}: {
  role: 'Baseline (left)' | 'Compared (right)';
  side: ComparisonSide;
}) {
  return (
    <div className="flex-1 min-w-0">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        {role}
      </p>
      <p className="mt-0.5 text-h3 text-navy truncate">{side.label || '—'}</p>
      <p className="mt-0.5 text-caption text-slate">
        {side.periodLabel}
        {side.reportingDate ? ` · ${fmtDateUTC(side.reportingDate)}` : ''}
      </p>
      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-micro text-slate tnum">
        {side.version != null && <span>v{side.version}</span>}
        <span>{labelize(side.scenarioCode)}</span>
        {side.engineVersion && <span>{side.engineVersion}</span>}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delta table — grouped, with a semantic (direction × favorability) indicator.
// ---------------------------------------------------------------------------

function DeltaLineRow({ line }: { line: ComparisonLine }) {
  return (
    <tr className="border-b border-border-light last:border-b-0 hover:bg-surface">
      <td className="py-1.5 px-4 align-middle">
        <span className="text-caption text-navy">{line.label}</span>
      </td>
      <td className="py-1.5 px-4 align-middle text-right num text-navy/85">
        {formatValue(line.unit, line.leftValue)}
      </td>
      <td className="py-1.5 px-4 align-middle text-right num text-navy">
        {formatValue(line.unit, line.rightValue)}
      </td>
      <td className="py-1.5 px-4 align-middle text-right num">
        <SemanticDelta
          direction={line.direction}
          favorability={line.favorability}
          className="text-caption justify-end"
        >
          {formatAbsDelta(line.unit, line.deltaCcy)}
        </SemanticDelta>
      </td>
      <td className="py-1.5 px-4 align-middle text-right num">
        {line.isNew ? (
          <span className="inline-flex items-center rounded border border-action/30 bg-action-light px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-action">
            new
          </span>
        ) : line.deltaPct === null ? (
          <span className="text-caption text-slate">—</span>
        ) : (
          <span
            className={`text-caption font-mono tnum ${
              line.favorability === 'favorable'
                ? 'text-success'
                : line.favorability === 'adverse'
                  ? 'text-critical'
                  : 'text-slate'
            }`}
          >
            {formatRelPct(line.deltaPct)}
          </span>
        )}
      </td>
      <td className="py-1.5 px-2 align-middle text-center">
        <SemanticDelta
          direction={line.direction}
          favorability={line.favorability}
          className="text-body justify-center"
        />
      </td>
    </tr>
  );
}

function DeltaTable({
  groups,
  leftLabel,
  rightLabel,
}: {
  groups: { title: string; lines: ComparisonLine[] }[];
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <div className="divide-y divide-border">
      {groups.map((group) => (
        <div key={group.title}>
          <div className="bg-surface px-4 py-2 border-b border-border">
            <h3 className="text-micro font-semibold text-navy uppercase tracking-wider">
              {group.title}
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-body border-collapse tnum">
              <thead>
                <tr className="border-b border-border-light">
                  <th className="py-1.5 px-4 text-left text-micro font-medium uppercase tracking-wider text-slate">
                    Line
                  </th>
                  <th className="py-1.5 px-4 text-right text-micro font-medium uppercase tracking-wider text-slate max-w-[10rem] truncate">
                    {leftLabel}
                  </th>
                  <th className="py-1.5 px-4 text-right text-micro font-medium uppercase tracking-wider text-slate max-w-[10rem] truncate">
                    {rightLabel}
                  </th>
                  <th className="py-1.5 px-4 text-right text-micro font-medium uppercase tracking-wider text-slate">
                    Δ
                  </th>
                  <th className="py-1.5 px-4 text-right text-micro font-medium uppercase tracking-wider text-slate">
                    Δ %
                  </th>
                  <th
                    className="py-1.5 px-2 w-10 text-center text-micro font-medium uppercase tracking-wider text-slate"
                    aria-label="Trend"
                  />
                </tr>
              </thead>
              <tbody>
                {group.lines.map((line) => (
                  <DeltaLineRow key={line.key} line={line} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers for the selector data.
// ---------------------------------------------------------------------------

function sortRunsNewestFirst(
  runs: RegulatoryRunSummaryRead[]
): RegulatoryRunSummaryRead[] {
  return [...runs].sort(
    (a, b) => b.createdAt.getTime() - a.createdAt.getTime()
  );
}

function sortPeriodsNewestFirst(
  periods: BankReportingPeriodRead[]
): BankReportingPeriodRead[] {
  return [...periods].sort(
    (a, b) => b.periodEnd.getTime() - a.periodEnd.getTime()
  );
}

function runOptionLabel(run: RegulatoryRunSummaryRead): string {
  return `${fmtTimestamp(run.createdAt)} · ${labelize(run.status)}`;
}

// ---------------------------------------------------------------------------
// Page.
// ---------------------------------------------------------------------------

export default function ComparePage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const [mode, setMode] = useState<ComparisonMode>('version');
  const [module, setModule] = useState<ComparisonModule>('liquidity');
  const [scenarioCode, setScenarioCode] = useState('baseline');

  // Version mode: a period, then two runs within it.
  const [versionPeriodId, setVersionPeriodId] = useState<string | null>(null);
  const [versionLeft, setVersionLeft] = useState<string | null>(null);
  const [versionRight, setVersionRight] = useState<string | null>(null);

  // Period mode: two periods.
  const [periodLeft, setPeriodLeft] = useState<string | null>(null);
  const [periodRight, setPeriodRight] = useState<string | null>(null);

  const periodsQuery = useReportingPeriods(bankId);
  // One module-scoped runs query feeds every selector: the scenario options, the
  // periods that actually have runs, and (client-filtered) the version run picks.
  const runsQuery = useRegulatoryRuns(bankId, { module, limit: 100 });

  const allRuns = useMemo(
    () => sortRunsNewestFirst(runsQuery.data?.runs ?? []),
    [runsQuery.data]
  );
  const allPeriods = useMemo(
    () => sortPeriodsNewestFirst(periodsQuery.data?.periods ?? []),
    [periodsQuery.data]
  );

  // Scenarios present for this module, baseline first.
  const scenarioOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const run of allRuns) seen.add(run.scenarioCode);
    const codes = [...seen];
    codes.sort((a, b) =>
      a === 'baseline' ? -1 : b === 'baseline' ? 1 : a.localeCompare(b)
    );
    return codes.length > 0 ? codes : ['baseline'];
  }, [allRuns]);

  // Periods that have at least one run for the selected module + scenario — the
  // only periods a valid comparison can be built from.
  const periodsWithRuns = useMemo(() => {
    const ids = new Set(
      allRuns
        .filter((r) => r.scenarioCode === scenarioCode)
        .map((r) => r.reportingPeriodId)
    );
    return allPeriods.filter((p) => ids.has(p.id));
  }, [allPeriods, allRuns, scenarioCode]);

  // Runs for the chosen period + scenario — the version-mode run picks.
  const versionRuns = useMemo(
    () =>
      allRuns.filter(
        (r) =>
          r.reportingPeriodId === versionPeriodId &&
          r.scenarioCode === scenarioCode
      ),
    [allRuns, versionPeriodId, scenarioCode]
  );

  // Keep the scenario valid as modules change.
  useEffect(() => {
    setScenarioCode((prev) =>
      scenarioOptions.includes(prev)
        ? prev
        : scenarioOptions.includes('baseline')
          ? 'baseline'
          : scenarioOptions[0]
    );
  }, [scenarioOptions]);

  // Version mode: default the period to the newest with runs.
  useEffect(() => {
    if (mode !== 'version') return;
    setVersionPeriodId((prev) =>
      prev && periodsWithRuns.some((p) => p.id === prev)
        ? prev
        : (periodsWithRuns[0]?.id ?? null)
    );
  }, [mode, periodsWithRuns]);

  // Version mode: default right = newest run, left = the one before it.
  useEffect(() => {
    if (mode !== 'version') return;
    if (versionRuns.length === 0) {
      setVersionRight(null);
      setVersionLeft(null);
      return;
    }
    setVersionRight((prev) =>
      prev && versionRuns.some((r) => r.id === prev) ? prev : versionRuns[0].id
    );
    setVersionLeft((prev) =>
      prev && versionRuns.some((r) => r.id === prev)
        ? prev
        : (versionRuns[1]?.id ?? versionRuns[0].id)
    );
  }, [mode, versionRuns]);

  // Period mode: default right = newest period, left = the one before it.
  useEffect(() => {
    if (mode !== 'period') return;
    if (periodsWithRuns.length === 0) {
      setPeriodRight(null);
      setPeriodLeft(null);
      return;
    }
    setPeriodRight((prev) =>
      prev && periodsWithRuns.some((p) => p.id === prev)
        ? prev
        : periodsWithRuns[0].id
    );
    setPeriodLeft((prev) =>
      prev && periodsWithRuns.some((p) => p.id === prev)
        ? prev
        : (periodsWithRuns[1]?.id ?? periodsWithRuns[0].id)
    );
  }, [mode, periodsWithRuns]);

  const left = mode === 'version' ? versionLeft : periodLeft;
  const right = mode === 'version' ? versionRight : periodRight;

  const comparison = useReportComparison(
    bankId,
    { mode, module, left: left ?? '', right: right ?? '', scenarioCode },
    Boolean(left && right)
  );

  const selectorsLoading = periodsQuery.isLoading || runsQuery.isLoading;
  const selectorsError = periodsQuery.error ?? runsQuery.error;

  // Nothing to compare: no runs at all, or (mode-specific) fewer than two picks.
  const noRuns = periodsWithRuns.length === 0;
  const notEnoughVersions =
    mode === 'version' && !noRuns && versionRuns.length < 2;
  const notEnoughPeriods =
    mode === 'period' && periodsWithRuns.length < 2;
  const sameSelection = Boolean(left && right && left === right);

  const leftColLabel = comparison.data
    ? comparison.data.left.version != null
      ? `v${comparison.data.left.version}`
      : comparison.data.left.periodLabel || 'Left'
    : 'Left';
  const rightColLabel = comparison.data
    ? comparison.data.right.version != null
      ? `v${comparison.data.right.version}`
      : comparison.data.right.periodLabel || 'Right'
    : 'Right';

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'Compare' },
        ]}
        title="Compare"
        subtitle="Line-by-line diff of two generated returns — server-computed, favorability-coloured deltas"
        action={<ModeToggle mode={mode} onChange={setMode} />}
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={selectorsLoading}
          error={selectorsError}
          onRetry={() => {
            void periodsQuery.refetch();
            void runsQuery.refetch();
          }}
        >
          {/* Selectors */}
          <SectionCard
            title="What to compare"
            subtitle={
              mode === 'version'
                ? 'Two run versions of the same reporting period'
                : 'One return across two reporting periods'
            }
          >
            <div className="flex flex-wrap items-end gap-4">
              <Selector
                label="Return family"
                value={module}
                onChange={(v) => setModule(v as ComparisonModule)}
              >
                {COMPARISON_MODULES.map((m) => (
                  <option key={m} value={m}>
                    {COMPARISON_MODULE_LABELS[m]}
                  </option>
                ))}
              </Selector>

              <Selector
                label="Scenario"
                value={scenarioCode}
                onChange={setScenarioCode}
                disabled={scenarioOptions.length <= 1}
              >
                {scenarioOptions.map((code) => (
                  <option key={code} value={code}>
                    {labelize(code)}
                  </option>
                ))}
              </Selector>

              {mode === 'version' ? (
                <>
                  <Selector
                    label="Reporting period"
                    value={versionPeriodId ?? ''}
                    onChange={setVersionPeriodId}
                    disabled={periodsWithRuns.length === 0}
                  >
                    {periodsWithRuns.length === 0 && (
                      <option value="">No periods with runs</option>
                    )}
                    {periodsWithRuns.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </Selector>
                  <Selector
                    label="Baseline version"
                    value={versionLeft ?? ''}
                    onChange={setVersionLeft}
                    disabled={versionRuns.length === 0}
                  >
                    {versionRuns.map((r) => (
                      <option key={r.id} value={r.id}>
                        {runOptionLabel(r)}
                      </option>
                    ))}
                  </Selector>
                  <ArrowLeftRight
                    size={16}
                    className="mb-2 text-slate shrink-0"
                    aria-hidden
                  />
                  <Selector
                    label="Compared version"
                    value={versionRight ?? ''}
                    onChange={setVersionRight}
                    disabled={versionRuns.length === 0}
                  >
                    {versionRuns.map((r) => (
                      <option key={r.id} value={r.id}>
                        {runOptionLabel(r)}
                      </option>
                    ))}
                  </Selector>
                </>
              ) : (
                <>
                  <Selector
                    label="Baseline period"
                    value={periodLeft ?? ''}
                    onChange={setPeriodLeft}
                    disabled={periodsWithRuns.length === 0}
                  >
                    {periodsWithRuns.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </Selector>
                  <ArrowLeftRight
                    size={16}
                    className="mb-2 text-slate shrink-0"
                    aria-hidden
                  />
                  <Selector
                    label="Compared period"
                    value={periodRight ?? ''}
                    onChange={setPeriodRight}
                    disabled={periodsWithRuns.length === 0}
                  >
                    {periodsWithRuns.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </Selector>
                </>
              )}
            </div>
          </SectionCard>

          {/* Guard states before hitting the diff */}
          {noRuns ? (
            <EmptyState
              Icon={Scale}
              title="No runs to compare"
              description={`No ${COMPARISON_MODULE_LABELS[module]} runs exist for the ${labelize(
                scenarioCode
              )} scenario yet. Generate returns from the Returns workspace, then compare their versions here.`}
            />
          ) : notEnoughVersions ? (
            <EmptyState
              Icon={GitCompareArrows}
              title="Only one version for this period"
              description="A version comparison needs at least two runs of the same period. Regenerate the return to produce another version, or switch to Period mode."
            />
          ) : notEnoughPeriods ? (
            <EmptyState
              Icon={GitCompareArrows}
              title="Only one period has runs"
              description={`A period comparison needs two reporting periods with ${COMPARISON_MODULE_LABELS[module]} runs for this scenario. Generate the return for another period first.`}
            />
          ) : sameSelection ? (
            <EmptyState
              Icon={GitCompareArrows}
              title="Pick two different sides"
              description="The baseline and compared selections are the same. Choose two distinct versions (or periods) to see a diff."
            />
          ) : (
            <ComparisonBody
              comparison={comparison}
              leftColLabel={leftColLabel}
              rightColLabel={rightColLabel}
            />
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// The result body — its own loading / error / empty handling so the selectors
// above stay interactive while the diff refetches.
// ---------------------------------------------------------------------------

function ComparisonBody({
  comparison,
  leftColLabel,
  rightColLabel,
}: {
  comparison: ReturnType<typeof useReportComparison>;
  leftColLabel: string;
  rightColLabel: string;
}) {
  if (comparison.isLoading) {
    return (
      <div className="space-y-4">
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }

  if (comparison.error) {
    const err = comparison.error;
    const notComparable =
      isApiError(err) && err.errorCode === 'not_comparable';
    const missing = isApiError(err) && err.status === 404;
    if (notComparable) {
      return (
        <ErrorPanel
          error={err}
          onRetry={() => comparison.refetch()}
          title="These two returns aren't comparable"
        />
      );
    }
    return (
      <ErrorPanel
        error={err}
        onRetry={() => comparison.refetch()}
        title={
          missing
            ? 'One of the selected runs or periods no longer exists'
            : 'Could not load the comparison'
        }
      />
    );
  }

  const data = comparison.data;
  if (!data) return null;

  const hasLines = data.groups.some((g) => g.lines.length > 0);

  return (
    <>
      {/* Summary header */}
      <SectionCard
        title="Comparison summary"
        subtitle={
          data.mode === 'version'
            ? 'Two versions of the same period'
            : 'One return across two periods'
        }
      >
        <div className="flex flex-col gap-5">
          <div className="flex flex-col sm:flex-row items-stretch gap-4">
            <SideCard role="Baseline (left)" side={data.left} />
            <div className="hidden sm:flex items-center">
              <GitCompareArrows size={18} className="text-slate" aria-hidden />
            </div>
            <SideCard role="Compared (right)" side={data.right} />
          </div>
          <div className="border-t border-border-light pt-4">
            <CountChips
              favorable={data.favorableCount}
              adverse={data.adverseCount}
              neutral={data.neutralCount}
            />
          </div>
        </div>
      </SectionCard>

      {/* Delta table */}
      <SectionCard
        title="Line-by-line delta"
        subtitle="Δ is the absolute change; Δ % the change versus the baseline. Colour reads favourability — green favourable, red adverse."
        noPadding
      >
        {hasLines ? (
          <DeltaTable
            groups={data.groups}
            leftLabel={leftColLabel}
            rightLabel={rightColLabel}
          />
        ) : (
          <div className="p-5">
            <EmptyState
              Icon={Scale}
              title="No line items in this diff"
              description="The two runs produced no comparable lines for this module."
            />
          </div>
        )}
      </SectionCard>
    </>
  );
}
