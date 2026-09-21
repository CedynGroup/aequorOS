"use client";

/**
 * The prescribed interest-rate shocks, and the disclosure grid.
 *
 * Two things a reader has to be able to see and cannot infer:
 *
 *   - which shocks count towards the outlier test and which are reported for
 *     completeness. The framework prescribes the whole set, so "why is this
 *     one not in the verdict" is a question the table has to answer itself;
 *   - the loss beside the arithmetic net. Losses do not net against gains
 *     across currencies, and showing only the loss hides the rule while
 *     showing only the net misstates the measure.
 *
 * Every shock's name comes from the server. A raw scenario code is never
 * printed — an unlabelled one says it is unnamed, which is a defect somebody
 * should see as a defect.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import type { StandardisedFramework } from "@/lib/api/irrbbSfNormalize";
import { money } from "./figures";
import { rows } from "./safe";
import { COLSPAN_FOUR, COLSPAN_SIX } from "./display";
import {
  SCENARIOS_HEADING,
  SCENARIOS_MEANING,
  TABLE8_HEADING,
  TABLE8_MEANING,
  UNNAMED_LINE,
  UNNAMED_SCENARIO,
  labelled,
} from "./labels";

export default function ScenariosPanel({
  view,
}: {
  view: StandardisedFramework;
}) {
  return (
    <>
      <SectionCard
        title={SCENARIOS_HEADING}
        subtitle={SCENARIOS_MEANING}
        noPadding
      >
        <table className="w-full">
          <thead className="bg-surface">
            <tr>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Shock
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Economic-value loss
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Arithmetic net
              </th>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                In the outlier test
              </th>
            </tr>
          </thead>
          <tbody>
            {rows(view.scenarios).length === 0 ? (
              <tr>
                <td
                  colSpan={COLSPAN_FOUR}
                  className="px-4 py-3 text-body text-slate"
                >
                  This result reports no shocks.
                </td>
              </tr>
            ) : (
              rows(view.scenarios).map((scenario, index) => (
                <tr
                  key={`${scenario.code}-${index}`}
                  className="border-t border-border-light"
                >
                  <td className="px-4 py-2 text-body text-navy">
                    {labelled(scenario.label, UNNAMED_SCENARIO)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {money(scenario.loss)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {money(scenario.net)}
                  </td>
                  <td className="px-4 py-2">
                    <StatusPill
                      tone={scenario.inOutlierSet ? "action" : "slate"}
                    >
                      {scenario.inOutlierSet ? "Counted" : "Reported only"}
                    </StatusPill>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </SectionCard>

      <SectionCard
        title={TABLE8_HEADING}
        subtitle={TABLE8_MEANING}
        noPadding
      >
        <table className="w-full">
          <thead className="bg-surface">
            <tr>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Line
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Change in economic value
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Prior period
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Arithmetic net
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Change in net interest income
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Prior period
              </th>
            </tr>
          </thead>
          <tbody>
            {rows(view.table8).length === 0 ? (
              <tr>
                <td
                  colSpan={COLSPAN_SIX}
                  className="px-4 py-3 text-body text-slate"
                >
                  This result reports no disclosure lines.
                </td>
              </tr>
            ) : (
              rows(view.table8).map((row, index) => (
                <tr
                  key={`${row.code}-${index}`}
                  className="border-t border-border-light"
                >
                  <td className="px-4 py-2 text-body text-navy">
                    {labelled(row.label, UNNAMED_LINE)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {money(row.deltaEve)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {money(row.deltaEvePrior)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {money(row.deltaEveNet)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {money(row.deltaNii)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {money(row.deltaNiiPrior)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </SectionCard>
    </>
  );
}
