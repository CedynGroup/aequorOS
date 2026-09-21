"use client";

/**
 * The capital plan's triggers, measured.
 *
 * A capital plan promises the Board that the institution will act at a named
 * level — an early warning, then an action level — for each capital ratio. A
 * promise nobody measures is not a control, so this screen measures it: the
 * current position, and every projected year of every scenario, against the
 * plan's own levels and against THAT YEAR's regulatory minimum.
 *
 * Three rules this screen keeps:
 *
 * NOTHING IS CORRECTED, ONLY REPORTED. A trigger set on the wrong side of the
 * regulatory minimum is a finding, shown in words. The plan belongs to the
 * Board, and a platform that quietly tightened it would be reporting something
 * the Board never agreed.
 *
 * AN UNEVALUATED TRIGGER IS NOT A CLEAR ONE. A ratio the plan names in words
 * the platform cannot match reads "Cannot be evaluated" — never green.
 *
 * EVERY MINIMUM COMES FROM THE CONTROL PLANE (D-024). The floors each year was
 * measured against are listed with their provenance, and none is written here.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapCapitalTriggers } from "@/lib/api/icaapRiskCapital";
import type { IcaapTriggerResult } from "@/lib/api/icaapRiskCapital";
import ParameterProvenance from "./ParameterProvenance";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import { COLSPAN_TWO } from "./display";
import {
  NOT_ASSESSED,
  NOT_AVAILABLE,
  fmtCount,
  fmtPercent,
  scenarioLabel,
  triggerFindingCopy,
  triggerMetricLabel,
  triggerStatusCopy,
} from "./labels";

export default function CapitalTriggers({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const query = useIcaapCapitalTriggers(bankId, cycleId);
  const evaluation = query.data;

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Capital plan triggers" message={unavailable} />;
  }

  const results = evaluation?.results ?? [];
  const findings = evaluation?.findings ?? [];

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {evaluation && (
        <div className="space-y-4">
          <SectionCard
            title="Capital plan triggers"
            subtitle={
              evaluation.planVersion === null
                ? "The levels the approved capital plan commits the institution to act at."
                : `The levels the approved capital plan commits the institution to act at — plan version ${fmtCount(evaluation.planVersion)}.`
            }
            footer={<ParameterProvenance uses={evaluation.floors} compact />}
            noPadding
          >
            {!evaluation.available ? (
              <div className="p-5">
                <EmptyState
                  title="The triggers have not been evaluated"
                  description={
                    evaluation.unavailableReason ??
                    "The capital plan this ICAAP rests on is not linked to the cycle yet."
                  }
                />
              </div>
            ) : results.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="The capital plan sets no triggers"
                  description="A trigger framework names, for each capital ratio, the level at which the institution gives itself an early warning and the level at which it acts. Add one to the capital plan and it is measured here."
                />
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-body">
                    <thead>
                      <tr className="border-b border-border-light text-caption text-slate">
                        <th scope="col" className="px-4 py-2 text-left">
                          Ratio
                        </th>
                        <th scope="col" className="px-4 py-2 text-right">
                          Early warning at
                        </th>
                        <th scope="col" className="px-4 py-2 text-right">
                          Action at
                        </th>
                        <th scope="col" className="px-4 py-2 text-right">
                          Position now
                        </th>
                        <th scope="col" className="px-4 py-2 text-left">
                          Where it stands
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.map((result) => (
                        <TriggerRow key={result.metricCode} result={result} />
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="px-4 py-3 text-caption text-slate">
                  {evaluation.triggersBreachedNow > 0
                    ? `${fmtCount(evaluation.triggersBreachedNow)} of ${fmtCount(evaluation.triggerCount)} triggers are at or beyond their action level today.`
                    : `No trigger is at its action level today. ${fmtCount(evaluation.triggerCount)} are measured.`}
                  {evaluation.firstActionYear !== null &&
                    ` On the plan's own projection, the first action level is reached in year ${fmtCount(evaluation.firstActionYear)}.`}
                </p>
              </>
            )}
          </SectionCard>

          {results.length > 0 && <ProjectedPath results={results} />}

          {findings.length > 0 && (
            <SectionCard
              title="What the trigger framework itself says"
              subtitle="These are observations about the plan's levels, not about this quarter's position."
            >
              <ul className="space-y-2 text-body text-navy">
                {findings.map((code) => (
                  <li key={code}>{triggerFindingCopy(code)}</li>
                ))}
              </ul>
            </SectionCard>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

function TriggerRow({ result }: { result: IcaapTriggerResult }) {
  const status = triggerStatusCopy(result.currentStatus);
  return (
    <tr className="border-b border-border-light/60 align-top">
      <th scope="row" className="px-4 py-2 text-left font-normal">
        <span className="block text-navy">
          {triggerMetricLabel(result.metricKey, result.metricCode)}
        </span>
        <span className="block text-caption text-slate">
          {result.direction === "higher_is_safer"
            ? "A higher value is safer"
            : "A lower value is safer"}
        </span>
      </th>
      <td className="px-4 py-2 text-right tnum">
        {fmtPercent(result.earlyWarningLevel, NOT_ASSESSED)}
      </td>
      <td className="px-4 py-2 text-right tnum">
        {fmtPercent(result.actionLevel, NOT_ASSESSED)}
      </td>
      <td className="px-4 py-2 text-right tnum">
        {fmtPercent(result.currentValue, NOT_AVAILABLE)}
      </td>
      <td className="px-4 py-2">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        {result.findings.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-caption text-warning">
            {result.findings.map((code) => (
              <li key={code}>{triggerFindingCopy(code)}</li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

/**
 * When, on the plan's own projection, each level is first reached.
 *
 * A blank cell means the level is not reached anywhere in the projection — it
 * is shown as an explicit "Not reached" rather than left empty, because an
 * empty cell in a capital table reads as missing data.
 */
function ProjectedPath({
  results,
}: {
  results: readonly IcaapTriggerResult[];
}) {
  const scenarios = Array.from(
    new Set(
      results.flatMap((result) => Object.keys(result.firstCrossing ?? {})),
    ),
  ).sort();

  if (scenarios.length === 0) {
    return (
      <SectionCard
        title="When the plan would act"
        subtitle="Taken from the capital plan's own projection."
      >
        <p className="text-body text-slate">
          The capital plan carries no projected path for these ratios, so no
          crossing year can be stated.
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="When the plan would act"
      subtitle="The first projected year each level is reached, by scenario."
      noPadding
    >
      <div className="overflow-x-auto">
        <table className="w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-caption text-slate">
              <th scope="col" className="px-4 py-2 text-left">
                Ratio
              </th>
              {scenarios.map((scenario) => (
                <th
                  key={scenario}
                  scope="col"
                  className="px-4 py-2 text-left"
                  colSpan={COLSPAN_TWO}
                >
                  {scenarioLabel(scenario)}
                </th>
              ))}
            </tr>
            <tr className="border-b border-border-light text-caption text-slate">
              <th scope="col" className="px-4 py-2 text-left">
                <span className="sr-only">Ratio</span>
              </th>
              {scenarios.flatMap((scenario) => [
                <th
                  key={`${scenario}-warn`}
                  scope="col"
                  className="px-4 py-2 text-right font-normal"
                >
                  Early warning
                </th>,
                <th
                  key={`${scenario}-act`}
                  scope="col"
                  className="px-4 py-2 text-right font-normal"
                >
                  Action
                </th>,
              ])}
            </tr>
          </thead>
          <tbody>
            {results.map((result) => (
              <tr
                key={result.metricCode}
                className="border-b border-border-light/60"
              >
                <th scope="row" className="px-4 py-2 text-left font-normal">
                  {triggerMetricLabel(result.metricKey, result.metricCode)}
                </th>
                {scenarios.flatMap((scenario) => {
                  const levels = (result.firstCrossing ?? {})[scenario] ?? {};
                  return [
                    <td
                      key={`${scenario}-warn`}
                      className="px-4 py-2 text-right tnum"
                    >
                      <CrossingYear year={levels.early_warning} />
                    </td>,
                    <td
                      key={`${scenario}-act`}
                      className="px-4 py-2 text-right tnum"
                    >
                      <CrossingYear year={levels.action} />
                    </td>,
                  ];
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

function CrossingYear({ year }: { year: number | undefined }) {
  if (year === undefined) {
    return <span className="text-slate">Not reached</span>;
  }
  return <span className="text-navy">Year {fmtCount(year)}</span>;
}
