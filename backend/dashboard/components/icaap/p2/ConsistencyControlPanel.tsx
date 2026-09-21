"use client";

/**
 * The source-consistency control: the same figure, as the ICAAP states it and
 * as another platform surface states it, compared LIKE FOR LIKE.
 *
 * Both sides of each pair carry the basis they are expressed in, because the
 * whole point of the control is that an apparent disagreement is usually a unit
 * or a basis difference. The verdict is the server's, decided against the
 * governed tolerance; the tolerance itself is shown with its provenance and is
 * never written into this file (D-024).
 *
 * A pair the platform cannot compare is `not_comparable`, which renders as
 * exactly that. It is never shown as agreement.
 */

import { useState } from "react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import type {
  IcaapConsistency,
  IcaapParameterUse,
} from "@/lib/api/icaapRiskCapital";
import ParameterProvenance from "./ParameterProvenance";
import { EXPLANATION_MAX, REASON_MAX, ROWS_MEDIUM } from "./display";
import {
  NOT_AVAILABLE,
  basisLabel,
  consistencyCopy,
  fmtPercent,
} from "./labels";

export default function ConsistencyControlPanel({
  title,
  subtitle,
  comparisons,
  parameters,
  canEdit,
  onExplain,
  isSaving = false,
}: {
  title: string;
  subtitle?: string;
  comparisons: readonly IcaapConsistency[] | null | undefined;
  parameters: readonly IcaapParameterUse[] | null | undefined;
  canEdit: boolean;
  /** Undefined when the surface has no explanation route (read-only view). */
  onExplain?: (comparisonKey: string, explanation: string, reason: string) => void;
  isSaving?: boolean;
}) {
  const [explaining, setExplaining] = useState<IcaapConsistency | null>(
    null,
  );
  const rows = comparisons ?? [];

  return (
    <SectionCard
      title={title}
      subtitle={subtitle}
      footer={<ParameterProvenance uses={parameters} compact />}
      noPadding
    >
      <div className="overflow-x-auto">
        <table className="w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-caption text-slate">
              <th scope="col" className="px-4 py-2 text-left">
                Figure
              </th>
              <th scope="col" className="px-4 py-2 text-left">
                Basis
              </th>
              <th scope="col" className="px-4 py-2 text-right">
                ICAAP
              </th>
              <th scope="col" className="px-4 py-2 text-left">
                Compared with
              </th>
              <th scope="col" className="px-4 py-2 text-right">
                Difference
              </th>
              <th scope="col" className="px-4 py-2 text-left">
                Verdict
              </th>
              <th scope="col" className="px-4 py-2 text-right">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((comparison) => {
              const verdict = consistencyCopy(comparison.status);
              return (
                <tr
                  key={comparison.comparisonKey}
                  className="border-b border-border-light/60 align-top"
                >
                  <th scope="row" className="px-4 py-2 text-left font-normal">
                    {comparison.row}
                  </th>
                  <td className="px-4 py-2 text-slate">
                    {basisLabel(comparison.basis)}
                  </td>
                  <td className="px-4 py-2 text-right tnum">
                    {comparison.icaap ?? NOT_AVAILABLE}
                  </td>
                  <td className="px-4 py-2">
                    <span className="block">{comparison.comparator}</span>
                    <span className="block tnum text-slate">
                      {comparison.other ?? NOT_AVAILABLE}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right tnum">
                    {fmtPercent(comparison.relativeDiffPct, NOT_AVAILABLE)}
                  </td>
                  <td className="px-4 py-2">
                    <StatusPill tone={verdict.tone}>{verdict.label}</StatusPill>
                    {comparison.explanation && (
                      <p className="mt-1 text-caption text-slate">
                        {comparison.explanation}
                        {!comparison.explanationCurrent && (
                          <span className="ml-1 text-warning">
                            (written against earlier figures)
                          </span>
                        )}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {canEdit && onExplain && (
                      <SecondaryButton onClick={() => setExplaining(comparison)}>
                        {comparison.explanation ? "Update reason" : "Explain"}
                      </SecondaryButton>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && (
        <p className="px-4 py-3 text-body text-slate">
          There is nothing to compare yet. The control runs once the Pillar 2
          register carries the figures it checks.
        </p>
      )}

      {explaining && onExplain && (
        <ExplainDialog
          comparison={explaining}
          isSaving={isSaving}
          onClose={() => setExplaining(null)}
          onSubmit={(explanation, reason) => {
            onExplain(explaining.comparisonKey, explanation, reason);
            setExplaining(null);
          }}
        />
      )}
    </SectionCard>
  );
}

function ExplainDialog({
  comparison,
  isSaving,
  onClose,
  onSubmit,
}: {
  comparison: IcaapConsistency;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (explanation: string, reason: string) => void;
}) {
  const [explanation, setExplanation] = useState(comparison.explanation ?? "");
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title={`Why ${comparison.row} differs`}
      description={`The ICAAP states ${comparison.icaap ?? NOT_AVAILABLE} and ${comparison.comparator} states ${comparison.other ?? NOT_AVAILABLE}, on the basis "${basisLabel(comparison.basis)}".`}
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              isSaving || explanation.trim() === "" || reason.trim() === ""
            }
            onClick={() => onSubmit(explanation.trim(), reason.trim())}
          >
            {isSaving ? "Saving…" : "Record the explanation"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        <FieldLabel
          label="Explanation"
          hint="A reviewer reads this next to the two figures. Say what accounts for the difference."
        >
          <textarea
            value={explanation}
            maxLength={EXPLANATION_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setExplanation(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Reason for this change" hint="Recorded in the audit trail.">
          <input
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
