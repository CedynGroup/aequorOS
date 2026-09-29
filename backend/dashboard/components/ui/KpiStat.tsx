import type { ReactNode } from 'react';
import { Info } from 'lucide-react';
import type { BiQuery } from '@aequoros/risk-service-api';
import DeltaBadge from './DeltaBadge';

export type KpiStatus = 'ok' | 'warn' | 'crit';

const edgeStyles: Record<KpiStatus, string> = {
  ok: 'inset 3px 0 0 rgb(var(--ok))',
  warn: 'inset 3px 0 0 rgb(var(--warn))',
  crit: 'inset 3px 0 0 rgb(var(--crit))',
};

/** The accessible name of the provenance affordance. Production copy. */
export const KPI_EXPLAIN_LABEL = 'Where this figure came from';

/**
 * A KPI value that is a PHRASE rather than a figure.
 *
 * The fail-closed states pass words where a number normally goes — "Not
 * computable", "Not assessed", "None". At the 28px mono KPI size a fourteen-
 * character phrase is wider than the tile and, because the value is
 * `whitespace-nowrap` with nothing clipping it, it spilled straight across the
 * card border into the neighbouring stat (seen on the SDI capital view's
 * "Capital headroom"). Truncating it instead would be worse: a half-shown
 * regulatory state reads as a rendering bug rather than a deliberate refusal.
 *
 * So a phrase is typeset one step down and allowed to wrap; anything carrying a
 * digit — every real figure, including "GHS 1.4B" and "15.83" — keeps the
 * tabular KPI treatment unchanged.
 */
function isPhraseValue(value: string | number): boolean {
  return typeof value === 'string' && !/\d/.test(value) && /[A-Za-z]{3,}/.test(value);
}

// ---------------------------------------------------------------------------
// Provenance: which BI measure a module KPI is a copy of
// ---------------------------------------------------------------------------

/**
 * What a module KPI opens when a reader asks where it came from: one catalogue
 * measure, read in one query. `components/bi/InsightStrip.tsx::useKpiExplain`
 * composes these two from the live catalogue and hands the resulting click
 * handler to `KpiStat`'s `explain` prop.
 */
export type KpiExplainTarget = Readonly<{ measure: string; query: BiQuery }>;

/**
 * The slice of `BiCatalogueMeasureRead` the resolver reads. Declared
 * structurally so this file stays free of the BI client and can be exercised
 * by a plain Node test.
 */
export type EngineMeasureLike = Readonly<{
  id: string;
  measureKind: string;
  engineMetricId?: string | null;
  engineTier?: string | null;
}>;

/**
 * The regime segment of an engine measure id, `engine.{metric}.{regime}.{tier}`.
 *
 * The same wire contract the compiler reads (`services/bi/compiler.py`): the
 * catalogue exposes the metric id and the tier as fields but not the regime, and
 * the regime is the whole question when a metric is multi-authority.
 */
function engineRegimeOf(id: string, metricId: string, tier: string): string | null {
  const prefix = `engine.${metricId}.`;
  const suffix = `.${tier}`;
  if (!id.startsWith(prefix) || !id.endsWith(suffix)) return null;
  const regime = id.slice(prefix.length, id.length - suffix.length);
  return regime.length > 0 && !regime.includes('.') ? regime : null;
}

/**
 * THE LIVE-TIER CATALOGUE COPY OF ONE REGISTRY METRIC, FOR THIS INSTITUTION — or
 * null, which means the KPI gets no provenance affordance at all.
 *
 * A module landing page shows the LIVE engine's figure (`LiveEngineNote`), and
 * BI keeps a copy of exactly that row as `engine.{metric}.{regime}.live`. The
 * catalogue is not narrowed to the tenant's regime, so a bank's catalogue lists
 * `car_pct` under CRD and under s.29 side by side — the same metric id, two
 * different laws, and only one of them ever has a row for this tenant
 * (`services/bi/insights/assemble.py::engine_measure_applies`). Opening the
 * other one would show a reviewer a declaration with no engine row under it and
 * call it the provenance of the figure on screen.
 *
 * So: one live copy resolves on its own (a single primary authority, whatever
 * regime — an accounting standard applies to both classes); several resolve
 * ONLY to the one whose regime is the institution's own capital regime; anything
 * else is refused. Refusal is the safe failure — the KPI simply carries no
 * button — and it is what an unknown capital regime gets too.
 */
export function liveEngineMeasureId(
  measures: readonly EngineMeasureLike[] | undefined,
  metricId: string,
  capitalRegime: string | null | undefined,
): string | null {
  if (!measures) return null;
  const copies = measures.filter(
    (measure) =>
      measure.measureKind === 'certified_engine' &&
      measure.engineMetricId === metricId &&
      measure.engineTier === 'live',
  );
  if (copies.length === 1) return copies[0].id;
  if (copies.length === 0 || !capitalRegime) return null;
  const own = copies.filter(
    (measure) => engineRegimeOf(measure.id, metricId, 'live') === capitalRegime,
  );
  return own.length === 1 ? own[0].id : null;
}

/**
 * The query a module KPI's figure is explained IN: that one measure, at the
 * page's reporting date, over the whole institution. No dimension, no filter —
 * a landing-page KPI is the institution-wide figure, and the server narrows the
 * read to the caller's own data scope regardless.
 */
export function kpiExplainQuery(measureId: string, asOf: string): BiQuery {
  return { measures: [measureId], time: { asOf } };
}

/**
 * Dense KPI stat for module dashboards: micro label, big tabular-numeral
 * value, optional unit, delta vs the prior period, a sparkline slot, and a
 * status edge-glow on the left border.
 *
 * `explain` is OPTIONAL and most KPIs in the app do not have one: they are not
 * BI measures. A KPI without it renders exactly as before. A KPI with it gains
 * one small affordance in the label row that opens the provenance drawer the
 * caller owns (`docs/bi.md` §Insights layer: "`KpiStat` gains an `explain` prop
 * that opens `ExplainDrawer`").
 */
export default function KpiStat({
  label,
  value,
  unit,
  delta,
  deltaSuffix = ' pts',
  deltaDecimals = 1,
  invertDelta = false,
  status,
  sparkline,
  hint,
  explain,
  className = '',
}: {
  label: string;
  /** Pre-formatted display value (string) or a raw number. */
  value: string | number;
  unit?: string;
  delta?: number;
  deltaSuffix?: string;
  deltaDecimals?: number;
  /** When true, a negative delta is the good outcome. */
  invertDelta?: boolean;
  /** Colors the left edge glow: ok / warn / crit. */
  status?: KpiStatus;
  /** Slot for a <Sparkline /> or any small inline visual. */
  sparkline?: ReactNode;
  /** Secondary caption under the value (threshold, basis, etc.). */
  hint?: ReactNode;
  /**
   * Opens where this figure came from. Set only for a KPI that is a copy of a
   * catalogue measure; see `useKpiExplain`. Absent, the tile is unchanged.
   */
  explain?: () => void;
  className?: string;
}) {
  const labelNode = (
    <p className="text-micro font-medium text-slate uppercase tracking-wider">
      {label}
    </p>
  );

  return (
    <div
      className={`card px-4 py-3.5 flex flex-col gap-1.5 min-w-0 ${className}`}
      style={status ? { boxShadow: edgeStyles[status] } : undefined}
    >
      {explain ? (
        <div className="flex items-start justify-between gap-2">
          {labelNode}
          <button
            type="button"
            onClick={explain}
            aria-label={KPI_EXPLAIN_LABEL}
            title={KPI_EXPLAIN_LABEL}
            className="-mr-1 -mt-0.5 shrink-0 rounded p-0.5 text-slate hover:bg-surface hover:text-navy"
          >
            <Info size={12} aria-hidden />
          </button>
        </div>
      ) : (
        labelNode
      )}

      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-1 min-w-0">
          <span
            className={`font-mono ${
              isPhraseValue(value)
                ? 'text-h2 whitespace-normal'
                : 'text-kpi whitespace-nowrap'
            } text-navy tnum`}
            title={typeof value === 'number' ? String(value) : value}
          >
            {typeof value === 'number' ? String(value) : value}
          </span>
          {unit && <span className="text-body text-slate shrink-0">{unit}</span>}
        </div>
        {sparkline && <div className="shrink-0 pb-1">{sparkline}</div>}
      </div>

      {(delta !== undefined || hint) && (
        <div className="flex items-center justify-between gap-2 min-w-0">
          {delta !== undefined ? (
            <DeltaBadge
              value={delta}
              suffix={deltaSuffix}
              decimals={deltaDecimals}
              invert={invertDelta}
            />
          ) : (
            <span />
          )}
          {hint && (
            <span className="text-caption text-slate">{hint}</span>
          )}
        </div>
      )}
    </div>
  );
}
