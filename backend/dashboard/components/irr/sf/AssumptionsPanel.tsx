"use client";

/**
 * How complete the measurement is, and what the figures rest on.
 *
 * THE TWO THINGS THIS PANEL REFUSES TO FLATTEN.
 *
 * 1. **A modelling default carries HOW OFTEN it was applied.** "Defaults were
 *    applied" is a badge, and a badge cannot tell a default used on one small
 *    placement apart from the same default used across most of the book. Those
 *    are different exposures, so each default is listed with the number of
 *    positions it covered — and an unreported tally says so rather than
 *    printing zero, which would read as "never used".
 *
 * 2. **A representative calibration says it is representative, on the row.**
 *    A representative value is the platform's own working number, not a
 *    published supervisory one, and every figure resting on it inherits that.
 *    The server sends both the flag and its own sentence; both are shown, so
 *    the screen cannot disagree with the API or with the signed PDF.
 *
 * Excluded positions are listed with their value for the same reason: a
 * position measured as zero and a position not measured at all look identical
 * in a total.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import type { StandardisedFramework } from "@/lib/api/irrbbSfNormalize";
import { count, money } from "./figures";
import { rows } from "./safe";
import { COLSPAN_THREE } from "./display";
import {
  APPLIED_TO,
  ASSUMPTIONS_HEADING,
  ASSUMPTIONS_MEANING,
  ASSUMPTIONS_NONE,
  CONFIRMED_CHIP,
  COUNT_NOT_REPORTED,
  DATA_QUALITY_HEADING,
  EXCLUSIONS_HEADING,
  EXCLUSIONS_MEANING,
  EXCLUSIONS_NONE,
  INSTRUMENTS_MEASURED,
  PARAMETERS_EMPTY,
  PARAMETERS_HEADING,
  PENDING_CHIP,
  PENDING_MEANING,
  POSITIONS,
  REPRESENTATIVE_CHIP,
  REPRESENTATIVE_MEANING,
  UNNAMED_DEFAULT,
  labelled,
} from "./labels";

/** "applied to 40 positions", or the honest absence. Never a zero. */
function applications(value: number | null): string {
  return value === null
    ? COUNT_NOT_REPORTED
    : `${APPLIED_TO} ${count(value)} ${POSITIONS}`;
}

export default function AssumptionsPanel({
  view,
}: {
  view: StandardisedFramework;
}) {
  const quality = view.dataQuality ?? {};
  const assumptions = rows(quality.assumptions);
  const exclusions = rows(quality.exclusions);
  const instrumentCount = quality.instrumentCount ?? null;
  const parameters = rows(view.parameters);
  const representative = parameters.some((row) => row.representative);
  const pending = parameters.some((row) => row.pendingConfirmation);

  return (
    <>
      <SectionCard
        title={DATA_QUALITY_HEADING}
        subtitle={ASSUMPTIONS_MEANING}
        footer={
          <span>
            {INSTRUMENTS_MEASURED}: {count(instrumentCount)}
          </span>
        }
      >
        <p className="text-caption font-medium uppercase tracking-wide text-slate">
          {ASSUMPTIONS_HEADING}
        </p>
        {assumptions.length === 0 ? (
          <p className="mt-1 text-body text-slate">{ASSUMPTIONS_NONE}</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {assumptions.map((row, index) => (
              <li
                key={`${row.marker}-${index}`}
                className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-light pb-2 last:border-b-0"
              >
                <span className="text-body text-navy">
                  {labelled(row.label, UNNAMED_DEFAULT)}
                </span>
                <span className="text-caption tabular-nums text-slate">
                  {applications(row.count)}
                </span>
              </li>
            ))}
          </ul>
        )}

        <p className="mt-5 text-caption font-medium uppercase tracking-wide text-slate">
          {EXCLUSIONS_HEADING}
        </p>
        <p className="mt-1 text-caption leading-relaxed text-slate">
          {EXCLUSIONS_MEANING}
        </p>
        {exclusions.length === 0 ? (
          <p className="mt-1 text-body text-slate">{EXCLUSIONS_NONE}</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {exclusions.map((row, index) => (
              <li
                key={`${row.marker}-${index}`}
                className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-light pb-2 last:border-b-0"
              >
                <span className="text-body text-navy">
                  {labelled(row.label, UNNAMED_DEFAULT)}
                </span>
                <span className="text-caption tabular-nums text-slate">
                  {applications(row.count)} · {money(row.amountReporting)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard
        title={PARAMETERS_HEADING}
        subtitle={
          representative
            ? REPRESENTATIVE_MEANING
            : pending
              ? PENDING_MEANING
              : undefined
        }
        noPadding
      >
        <table className="w-full">
          <thead className="bg-surface">
            <tr>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Input
              </th>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Value
              </th>
              <th className="px-4 py-2 text-left text-caption font-medium text-slate">
                Provenance
              </th>
            </tr>
          </thead>
          <tbody>
            {parameters.length === 0 ? (
              <tr>
                <td
                  colSpan={COLSPAN_THREE}
                  className="px-4 py-3 text-body text-slate"
                >
                  {PARAMETERS_EMPTY}
                </td>
              </tr>
            ) : (
              parameters.map((row, index) => (
                <tr
                  key={`${row.code}-${index}`}
                  className="border-t border-border-light align-top"
                >
                  <td className="px-4 py-2 text-body text-navy">{row.label}</td>
                  <td className="px-4 py-2 text-body tabular-nums text-navy">
                    {row.value}
                    {row.unit === null ? "" : ` ${row.unit}`}
                  </td>
                  <td className="px-4 py-2 text-caption text-slate">
                    <span className="flex flex-wrap gap-1">
                      {row.representative ? (
                        <StatusPill tone="approaching">
                          {REPRESENTATIVE_CHIP}
                        </StatusPill>
                      ) : null}
                      {row.pendingConfirmation ? (
                        <StatusPill tone="pending">{PENDING_CHIP}</StatusPill>
                      ) : null}
                      {!row.representative && !row.pendingConfirmation ? (
                        <StatusPill tone="compliant">
                          {CONFIRMED_CHIP}
                        </StatusPill>
                      ) : null}
                    </span>
                    {row.statement === null ? null : (
                      <span className="mt-1 block leading-relaxed">
                        {row.statement}
                      </span>
                    )}
                    {row.sourceCitation === null ? null : (
                      <span className="mt-1 block">{row.sourceCitation}</span>
                    )}
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
