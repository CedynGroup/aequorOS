"use client";

/**
 * Every governed value this ICAAP rests on, in one place, with its provenance.
 *
 * D-024 makes this screen possible and necessary in the same breath: no
 * regulatory or methodological number is written into the platform's code, so
 * every threshold, band, coefficient and tolerance the assessment used is a
 * control-plane row an administrator can edit — and a reader is owed the list.
 *
 * What it shows for each value: the code, the value as at the cycle's own date,
 * the citation the control plane recorded, and whether the calibration is
 * confirmed or still a representative one pending confirmation (D-039).
 *
 * What it never does is fill a gap. A code the control plane governs no value
 * for is listed as missing, by name, because the calculations that need it will
 * refuse rather than substitute one (D-024 §4).
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapParameterRegister } from "@/lib/api/icaapRiskCapital";
import type { IcaapParameterUse } from "@/lib/api/icaapRiskCapital";
import { fmtDateValue } from "../format";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import { JSON_INDENT } from "./display";
import {
  NOT_AVAILABLE,
  PENDING_CONFIRMATION,
  REPRESENTATIVE_CALIBRATION,
  fmtInUnit,
  missingParameterSentence,
  parameterRoleLabel,
} from "./labels";

export default function ParameterRegister({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const query = useIcaapParameterRegister(bankId, cycleId);
  const register = query.data;

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Governed values" message={unavailable} />;
  }

  const parameters = register?.parameters ?? [];
  const missing = register?.missing ?? [];

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {register && (
        <div className="space-y-4">
          {missing.length > 0 && (
            <SectionCard
              title="Values the control plane does not hold"
              subtitle="Nothing is assumed in their place."
            >
              <p className="text-body text-navy">
                {missingParameterSentence(missing)}
              </p>
            </SectionCard>
          )}

          <SectionCard
            title="Governed values used by this assessment"
            subtitle={
              register.asOf
                ? `As at ${fmtDateValue(register.asOf)}. Every value is a control-plane record, not a number written into the platform.`
                : "Every value is a control-plane record, not a number written into the platform."
            }
            noPadding
          >
            {parameters.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="No governed values are recorded for this cycle"
                  description="Once the assessment computes a figure that rests on a governed value, the value and its citation are listed here."
                />
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-body">
                  <thead>
                    <tr className="border-b border-border-light text-caption text-slate">
                      <th scope="col" className="px-4 py-2 text-left">
                        Value
                      </th>
                      <th scope="col" className="px-4 py-2 text-left">
                        Used for
                      </th>
                      <th scope="col" className="px-4 py-2 text-left">
                        In force from
                      </th>
                      <th scope="col" className="px-4 py-2 text-left">
                        How far it can be relied on
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {parameters.map((parameter) => (
                      <ParameterRow
                        key={parameter.paramCode}
                        parameter={parameter}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
        </div>
      )}
    </QueryBoundary>
  );
}

function ParameterRow({ parameter }: { parameter: IcaapParameterUse }) {
  const structured = parameter.valueJson;
  return (
    <tr className="border-b border-border-light/60 align-top">
      <th scope="row" className="px-4 py-2 text-left font-normal">
        <span className="block text-navy">
          {structured === null
            ? fmtInUnit(parameter.value, parameter.unit, NOT_AVAILABLE)
            : "A table of values"}
        </span>
        <span className="block text-caption text-slate">
          {parameter.paramCode}
        </span>
        {structured !== null && (
          <details className="mt-1">
            <summary className="cursor-pointer text-caption text-slate">
              Show the table
            </summary>
            <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-surface p-2 text-caption text-navy">
              {JSON.stringify(structured, null, JSON_INDENT)}
            </pre>
          </details>
        )}
      </th>
      <td className="px-4 py-2 text-slate">
        {parameter.roles.length === 0
          ? "Not stated"
          : parameter.roles.map(parameterRoleLabel).join(", ")}
      </td>
      <td className="px-4 py-2 text-slate">
        {fmtDateValue(parameter.effectiveFrom, "Not stated")}
      </td>
      <td className="px-4 py-2">
        {!parameter.resolved ? (
          <StatusPill tone="critical">No governed value</StatusPill>
        ) : parameter.representative ? (
          <StatusPill tone="amber">{REPRESENTATIVE_CALIBRATION}</StatusPill>
        ) : parameter.confirmationStatus === "pending" ? (
          <StatusPill tone="amber">{PENDING_CONFIRMATION}</StatusPill>
        ) : (
          <StatusPill tone="slate">Confirmed</StatusPill>
        )}
        {parameter.sourceCitation && (
          <p className="mt-1 text-caption text-slate">
            {parameter.sourceCitation}
          </p>
        )}
      </td>
    </tr>
  );
}
