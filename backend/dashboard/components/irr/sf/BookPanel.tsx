"use client";

/**
 * The book the framework measured: which currencies were in scope and on what
 * curve, how the non-maturity deposits were split, and the repricing ladder
 * behind both.
 *
 * The two things this panel exists to make visible:
 *
 *   - **the curve each currency was measured on, with its date and source.**
 *     A framework result is only as good as the curves under it, and a stale
 *     or substituted curve is invisible in the headline figure;
 *   - **which deposit categories hit the governed core cap.** When the cap
 *     binds, the cap — not the institution's own behavioural view — is what
 *     decides the repricing profile, and that changes how the result should be
 *     read.
 *
 * Currencies excluded by the materiality test are named rather than dropped:
 * a currency that is not there and a currency that was judged immaterial look
 * identical in a table that only lists what it measured.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import type { StandardisedFramework } from "@/lib/api/irrbbSfNormalize";
import { day, moneyIn, pct, rate, years } from "./figures";
import { rows } from "./safe";
import { COLSPAN_FOUR, COLSPAN_SIX } from "./display";
import {
  CURRENCIES_EXCLUDED,
  CURRENCIES_HEADING,
  CURRENCIES_MEANING,
  LADDER_HEADING,
  LADDER_MEANING,
  NMD_HEADING,
  NMD_MEANING,
  NOT_REPORTED,
  TABLE7_HEADING,
  UNNAMED_BUCKET,
  UNNAMED_CATEGORY,
  labelled,
} from "./labels";

export default function BookPanel({ view }: { view: StandardisedFramework }) {
  return (
    <>
      <SectionCard
        title={CURRENCIES_HEADING}
        subtitle={CURRENCIES_MEANING}
        noPadding
        footer={
          rows(view.excludedCurrencies).length === 0 ? undefined : (
            <span>
              {CURRENCIES_EXCLUDED} {rows(view.excludedCurrencies).join(", ")}
            </span>
          )
        }
      >
        <table className="w-full">
          <thead className="bg-surface">
            <tr>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Currency
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Share of the book
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Rate to the reporting currency
              </th>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Curve
              </th>
            </tr>
          </thead>
          <tbody>
            {rows(view.currencies).length === 0 ? (
              <tr>
                <td
                  colSpan={COLSPAN_FOUR}
                  className="px-4 py-3 text-body text-slate"
                >
                  This result names no currencies.
                </td>
              </tr>
            ) : (
              rows(view.currencies).map((row, index) => (
                <tr
                  key={`${row.currency}-${index}`}
                  className="border-t border-border-light align-top"
                >
                  <td className="px-4 py-2 text-body text-navy">
                    <span className="font-medium">{row.currency}</span>
                    <StatusPill
                      tone={row.material ? "action" : "slate"}
                      className="ml-2"
                    >
                      {row.material ? "Material" : "Below the test"}
                    </StatusPill>
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {pct(row.sharePct)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {rate(row.fxToReporting)}
                  </td>
                  <td className="px-4 py-2 text-body text-slate">
                    <span className="text-navy">
                      {row.curveName ?? NOT_REPORTED}
                    </span>
                    <span className="block text-caption">
                      {day(row.curveAsOf)}
                      {row.curveSource === null ? "" : ` · ${row.curveSource}`}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </SectionCard>

      <SectionCard
        title={NMD_HEADING}
        subtitle={NMD_MEANING}
        noPadding
        footer={
          <span>
            {TABLE7_HEADING} — average{" "}
            {years(view.table7Quantitative?.averageRepricingMaturityYears ?? null)},
            longest{" "}
            {years(view.table7Quantitative?.longestRepricingMaturityYears ?? null)}
          </span>
        }
      >
        <table className="w-full">
          <thead className="bg-surface">
            <tr>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Category
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Balance
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Core
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Non-core
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Core cap
              </th>
              <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                Average core maturity
              </th>
            </tr>
          </thead>
          <tbody>
            {rows(view.nmdCategories).length === 0 ? (
              <tr>
                <td
                  colSpan={COLSPAN_SIX}
                  className="px-4 py-3 text-body text-slate"
                >
                  This result reports no non-maturity deposit categories.
                </td>
              </tr>
            ) : (
              rows(view.nmdCategories).map((row, index) => (
                <tr
                  key={`${row.currency}-${row.category}-${index}`}
                  className="border-t border-border-light"
                >
                  <td className="px-4 py-2 text-body text-navy">
                    {labelled(row.label, UNNAMED_CATEGORY)}
                    <span className="ml-2 text-caption text-slate">
                      {row.currency}
                    </span>
                    {row.capBinding ? (
                      <StatusPill tone="approaching" className="ml-2">
                        Cap binding
                      </StatusPill>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {moneyIn(row.balance, row.currency)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                    {moneyIn(row.core, row.currency)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {moneyIn(row.nonCore, row.currency)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {pct(row.coreCapPct)}
                  </td>
                  <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                    {years(row.averageCoreMaturityYears)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </SectionCard>

      <SectionCard title={LADDER_HEADING} subtitle={LADDER_MEANING} noPadding>
        <div className="max-h-96 overflow-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-surface">
              <tr>
                <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                  Currency
                </th>
                <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                  Time bucket
                </th>
                <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                  Principal
                </th>
                <th className="px-4 py-2 text-right text-caption font-medium text-slate">
                  Interest
                </th>
              </tr>
            </thead>
            <tbody>
              {rows(view.ladders).length === 0 ? (
                <tr>
                  <td
                    colSpan={COLSPAN_FOUR}
                    className="px-4 py-3 text-body text-slate"
                  >
                    This result reports no repricing flows.
                  </td>
                </tr>
              ) : (
                rows(view.ladders).map((row, index) => (
                  <tr
                    key={`${row.currency}-${row.bucketKey}-${index}`}
                    className="border-t border-border-light"
                  >
                    <td className="px-4 py-2 text-body text-navy">
                      {row.currency}
                    </td>
                    <td className="px-4 py-2 text-body text-slate">
                      {labelled(row.bucketLabel, UNNAMED_BUCKET)}
                    </td>
                    <td className="px-4 py-2 text-right text-body tabular-nums text-navy">
                      {moneyIn(row.principal, row.currency)}
                    </td>
                    <td className="px-4 py-2 text-right text-body tabular-nums text-slate">
                      {moneyIn(row.interest, row.currency)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </>
  );
}
