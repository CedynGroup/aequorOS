"use client";

/**
 * The framework's headline: the economic-value risk measure, what it is as a
 * share of Tier 1, and the outlier verdict beside the governed threshold it
 * was judged against.
 *
 * Two rules this panel carries:
 *
 *   - the verdict is stated only when the measure AND the threshold are both
 *     present. A measure judged against a missing threshold is a pass nobody
 *     granted, and the reader is told exactly that instead;
 *   - the loss and the arithmetic net sit side by side. The framework does not
 *     let a gain in one currency offset a loss in another, and a reader who
 *     cannot see both cannot see what that rule cost.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import type { SfMeasure, StandardisedFramework } from "@/lib/api/irrbbSfNormalize";
import { money, pct } from "./figures";
import { COLSPAN_THREE } from "./display";
import {
  MEASURES_HEADING,
  MEASURES_MEANING,
  NOT_REPORTED,
  OUTLIER_HEADING,
  UNNAMED_MEASURE,
  UNNAMED_SCENARIO,
  labelled,
  outlierSentence,
  outlierTone,
} from "./labels";

function MeasureRow({ measure }: { measure: SfMeasure }) {
  return (
    <tr className="border-t border-border-light">
      <td className="px-4 py-2 text-body text-navy">
        {labelled(measure.label, UNNAMED_MEASURE)}
      </td>
      <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
        {money(measure.measure)}
      </td>
      <td className="px-4 py-2 text-body text-slate">
        {measure.worstScenario === null && measure.worstScenarioLabel === null
          ? NOT_REPORTED
          : labelled(measure.worstScenarioLabel, UNNAMED_SCENARIO)}
      </td>
    </tr>
  );
}

export default function MeasuresPanel({
  view,
}: {
  view: StandardisedFramework;
}) {
  const sets = view.measures ?? {};
  const measures = [sets.outlierSet, sets.mandatory, sets.allScenarios].filter(
    (measure): measure is SfMeasure => Boolean(measure),
  );
  return (
    <SectionCard
      title={MEASURES_HEADING}
      subtitle={MEASURES_MEANING}
      noPadding
    >
      <div className="grid gap-px bg-border-light sm:grid-cols-3">
        <div className="bg-white px-4 py-3">
          <p className="text-caption text-slate">
            {labelled(sets.outlierSet?.label ?? null, UNNAMED_MEASURE)}
          </p>
          <p className="mt-1 text-h3 tabular-nums text-navy">
            {money(sets.outlierSet?.measure ?? null)}
          </p>
        </div>
        <div className="bg-white px-4 py-3">
          <p className="text-caption text-slate">Share of Tier 1 capital</p>
          <p className="mt-1 text-h3 tabular-nums text-navy">
            {pct(view.pctTier1)}
          </p>
        </div>
        <div className="bg-white px-4 py-3">
          <p className="text-caption text-slate">Tier 1 capital</p>
          <p className="mt-1 text-h3 tabular-nums text-navy">
            {money(view.tier1)}
          </p>
        </div>
      </div>

      <div className="border-t border-border-light px-4 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body font-medium text-navy">{OUTLIER_HEADING}</p>
          <StatusPill tone={outlierTone(view.outlierAssessable, view.outlier)}>
            {view.outlierAssessable
              ? view.outlier
                ? "Above threshold"
                : "Below threshold"
              : "Not assessed"}
          </StatusPill>
        </div>
        <p className="mt-1 text-body leading-relaxed text-navy/80">
          {outlierSentence(view.outlierAssessable, view.outlier)}
        </p>
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-caption text-slate">
          <div className="flex gap-1">
            <dt>Measured</dt>
            <dd className="tabular-nums text-navy">{pct(view.pctTier1)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Supervisory threshold</dt>
            <dd className="tabular-nums text-navy">
              {pct(view.outlierThresholdPct)}
            </dd>
          </div>
        </dl>
      </div>

      <table className="w-full">
        <thead className="bg-surface">
          <tr>
            <th className="px-4 py-2 text-left text-caption font-medium text-slate">
              Measure set
            </th>
            <th className="px-4 py-2 text-right text-caption font-medium text-slate">
              Largest loss
            </th>
            <th className="px-4 py-2 text-left text-caption font-medium text-slate">
              Worst shock
            </th>
          </tr>
        </thead>
        <tbody>
          {measures.length === 0 ? (
            <tr>
              <td
                colSpan={COLSPAN_THREE}
                className="px-4 py-3 text-body text-slate"
              >
                {NOT_REPORTED}
              </td>
            </tr>
          ) : (
            measures.map((measure, index) => (
              <MeasureRow key={`${measure.name}-${index}`} measure={measure} />
            ))
          )}
        </tbody>
      </table>
    </SectionCard>
  );
}
