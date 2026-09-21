"use client";

/**
 * The reconciliation: what the ICAAP says the bank NEEDS, against what it says
 * the bank HAS, with the eligibility of each resource stated.
 *
 * M1/D-015 splits this deliberately in two, and the split is visible here
 * because the two halves answer different questions and fail in different ways:
 *
 *   - the REQUIREMENT side compares Pillar 1 and Pillar 2 by risk, and a line
 *     whose difference the server flags must carry an explanation;
 *   - the RESOURCES side lists capital components with an explicit eligible /
 *     not-eligible flag, and the DB CHECK requires an explanation whenever a
 *     component is ineligible or differs from the regulatory figure. The form
 *     mirrors that check so the operator is told before the save is refused.
 *
 * Coverage (resources ÷ requirement) is computed by the SERVER and arrives with
 * `assessed` and a reason. When it is not assessed the screen says so — it
 * never divides two numbers itself, and it never shows an unassessed coverage
 * as adequate.
 */

import { useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  useComputeIcaapRequirementReconciliation,
  useExplainIcaapControlDifference,
  useExplainIcaapRequirementLine,
  useIcaapReconciliation,
  useLoadIcaapRegulatoryCapitalComponents,
  type IcaapRequirementLine,
} from "@/lib/api/icaapRiskCapital";
import ConsistencyControlPanel from "./ConsistencyControlPanel";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import {
  COLSPAN_TWO,
  EXPLANATION_MAX,
  ICON_SM,
  REASON_MAX,
  ROWS_MEDIUM,
} from "./display";
import {
  NOT_ASSESSED,
  NOT_AVAILABLE,
  fmtAmount,
  fmtPercent,
  tierLabel,
} from "./labels";

export default function CapitalReconciliation({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canEdit = scope.capitalEdit === true;

  const reconciliationQuery = useIcaapReconciliation(bankId, cycleId);
  const recompute = useComputeIcaapRequirementReconciliation(bankId, cycleId);
  const loadRegulatory = useLoadIcaapRegulatoryCapitalComponents(
    bankId,
    cycleId,
  );
  const explainLine = useExplainIcaapRequirementLine(bankId, cycleId);
  const explainControl = useExplainIcaapControlDifference(bankId, cycleId);

  const [explaining, setExplaining] = useState<IcaapRequirementLine | null>(
    null,
  );

  const data = reconciliationQuery.data;
  const requirementLines = data?.requirement?.lines ?? [];
  const requirementTotals = data?.requirement?.totals;
  const resourceLines = data?.resources?.lines ?? [];
  const resourceTotals = data?.resources?.totals;
  const coverage = data?.resources?.totals;
  const controls = data?.controls ?? [];

  const unavailable = p2UnavailableNotice(reconciliationQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Capital reconciliation" message={unavailable} />;
  }

  return (
    <QueryBoundary
      isLoading={reconciliationQuery.isLoading}
      error={reconciliationQuery.error}
      onRetry={() => void reconciliationQuery.refetch()}
      contained
    >
      {data && (
        <div className="space-y-4">
          <SectionCard
            title="Capital requirement"
            subtitle="Pillar 1 against the ICAAP's own assessment, by risk. Like for like."
            actions={
              canEdit ? (
                <SecondaryButton
                  disabled={recompute.isPending}
                  onClick={() =>
                    recompute.mutate({
                      reason: "Recompute the requirement reconciliation",
                    })
                  }
                >
                  <RefreshCw size={ICON_SM} aria-hidden />
                  {recompute.isPending ? "Computing…" : "Recompute"}
                </SecondaryButton>
              ) : undefined
            }
            noPadding
          >
            {data.requirement?.stale && (
              <p className="border-b border-border-light bg-warning-light/40 px-4 py-2 text-caption text-navy/80">
                These lines were computed before the current Pillar 2 figures.
                Recompute before relying on them.
              </p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="border-b border-border-light text-caption text-slate">
                    <th scope="col" className="px-4 py-2 text-left">
                      Risk
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      Pillar 1
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      ICAAP
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      Difference
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Explanation
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {requirementLines.map((line) => (
                    <tr
                      key={line.lineKey}
                      className="border-b border-border-light/60 align-top"
                    >
                      <th scope="row" className="px-4 py-2 text-left font-normal">
                        {line.label}
                      </th>
                      <td className="px-4 py-2 text-right tnum">
                        {fmtAmount(line.regulatoryAmount)}
                      </td>
                      <td className="px-4 py-2 text-right tnum">
                        {fmtAmount(line.internalAmount)}
                      </td>
                      <td className="px-4 py-2 text-right tnum">
                        {fmtAmount(line.difference, NOT_AVAILABLE)}
                      </td>
                      <td className="px-4 py-2">
                        {line.explanation ? (
                          <>
                            <span>{line.explanation}</span>
                            {!line.explanationCurrent && (
                              <span className="ml-1 text-caption text-warning">
                                (written against earlier figures)
                              </span>
                            )}
                          </>
                        ) : line.explanationRequired ? (
                          <StatusPill tone="breach">
                            Explanation required
                          </StatusPill>
                        ) : (
                          <span className="text-slate">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {canEdit && (
                          <SecondaryButton onClick={() => setExplaining(line)}>
                            {line.explanation ? "Update" : "Explain"}
                          </SecondaryButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-border text-body font-medium">
                    <th scope="row" className="px-4 py-2 text-left">
                      Total
                    </th>
                    <td className="px-4 py-2 text-right tnum">
                      {fmtAmount(requirementTotals?.totalRegulatoryRequirement)}
                    </td>
                    <td className="px-4 py-2 text-right tnum">
                      {fmtAmount(requirementTotals?.totalInternalRequirement)}
                    </td>
                    <td className="px-4 py-2 text-right tnum">
                      {fmtAmount(requirementTotals?.difference, NOT_AVAILABLE)}
                    </td>
                    <td colSpan={COLSPAN_TWO} />
                  </tr>
                </tfoot>
              </table>
            </div>
            {recompute.isError && (
              <p className="px-4 py-2 text-caption text-critical">
                {(recompute.error as Error).message}
              </p>
            )}
          </SectionCard>

          <SectionCard
            title="Capital resources"
            subtitle="What the bank holds, and whether each component is eligible for the ICAAP's own purposes."
            actions={
              canEdit ? (
                <SecondaryButton
                  disabled={loadRegulatory.isPending}
                  onClick={() =>
                    loadRegulatory.mutate({
                      reason: "Load the regulatory capital components",
                    })
                  }
                >
                  <Download size={ICON_SM} aria-hidden />
                  {loadRegulatory.isPending
                    ? "Loading…"
                    : "Load regulatory components"}
                </SecondaryButton>
              ) : undefined
            }
            noPadding
          >
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="border-b border-border-light text-caption text-slate">
                    <th scope="col" className="px-4 py-2 text-left">
                      Component
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Tier
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      Regulatory
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      ICAAP
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Eligible
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Explanation
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {resourceLines.map((line) => (
                    <tr
                      key={line.lineId}
                      className="border-b border-border-light/60 align-top"
                    >
                      <th scope="row" className="px-4 py-2 text-left font-normal">
                        {line.label}
                        <span className="block text-caption text-slate">
                          {line.origin === "regulatory_component"
                            ? "From the regulatory capital run"
                            : "Entered by the bank"}
                        </span>
                      </th>
                      <td className="px-4 py-2">{tierLabel(line.tier)}</td>
                      <td className="px-4 py-2 text-right tnum">
                        {fmtAmount(line.regulatoryAmount)}
                      </td>
                      <td className="px-4 py-2 text-right tnum">
                        {fmtAmount(line.internalAmount)}
                      </td>
                      <td className="px-4 py-2">
                        <StatusPill
                          tone={line.regulatoryEligible ? "compliant" : "critical"}
                        >
                          {line.regulatoryEligible ? "Recognised" : "Not recognised"}
                        </StatusPill>
                      </td>
                      <td className="px-4 py-2">
                        {line.explanation ??
                          (line.explanationRequired ? (
                            <StatusPill tone="breach">
                              Explanation required
                            </StatusPill>
                          ) : (
                            <span className="text-slate">—</span>
                          ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-border font-medium">
                    <th scope="row" className="px-4 py-2 text-left">
                      Total
                    </th>
                    <td />
                    <td className="px-4 py-2 text-right tnum">
                      {fmtAmount(resourceTotals?.regulatoryTotalCapital)}
                    </td>
                    <td className="px-4 py-2 text-right tnum">
                      {fmtAmount(resourceTotals?.availableInternalCapital)}
                    </td>
                    <td className="px-4 py-2 text-caption text-slate" colSpan={COLSPAN_TWO}>
                      Recognised:{" "}
                      {fmtAmount(resourceTotals?.recognisedRegulatoryCapital)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>

            <div className="border-t border-border-light px-4 py-3">
              <p className="text-caption text-slate">
                Internal capital coverage of the requirement
              </p>
              {coverage?.internalCapitalCoveragePct !== null &&
              coverage?.internalCapitalCoveragePct !== undefined ? (
                <p className="text-h3 tnum text-navy">
                  {fmtPercent(coverage.internalCapitalCoveragePct)}
                </p>
              ) : (
                <>
                  <p className="text-h3 text-slate">{NOT_ASSESSED}</p>
                  <p className="text-caption text-slate">
                    The platform has not computed a coverage for this cycle. It is
                    not derived here from the totals above.
                  </p>
                </>
              )}
              {coverage?.internalCapitalSurplus !== null &&
                coverage?.internalCapitalSurplus !== undefined && (
                  <p className="mt-1 text-caption text-slate">
                    Surplus over the requirement:{" "}
                    {fmtAmount(coverage.internalCapitalSurplus)}
                  </p>
                )}
              {coverage?.matchesRegulatoryTotal === false && (
                <p className="mt-1 text-caption text-warning">
                  The recognised total does not match the regulatory capital
                  total. Explain the difference on the lines above.
                </p>
              )}
            </div>

            {loadRegulatory.isError && (
              <p className="px-4 py-2 text-caption text-critical">
                {(loadRegulatory.error as Error).message}
              </p>
            )}
          </SectionCard>

          <ConsistencyControlPanel
            title="Source consistency"
            subtitle="The same figures, as the ICAAP states them and as the rest of the platform states them."
            comparisons={controls}
            parameters={data.parameters}
            canEdit={canEdit}
            isSaving={explainControl.isPending}
            onExplain={(comparisonKey, explanation, reason) =>
              explainControl.mutate({
                // The wire keys an explanation by control code + comparison; the
                // comparison key carries its control, so they are the same token
                // here rather than two the screen could get out of step.
                controlCode: comparisonKey.split(":")[0],
                comparisonKey,
                payload: { explanation, reason },
              })
            }
          />
        </div>
      )}

      {explaining && (
        <ExplainLineDialog
          line={explaining}
          isSaving={explainLine.isPending}
          onClose={() => setExplaining(null)}
          onSubmit={(explanation, reason) => {
            explainLine.mutate(
              { lineKey: explaining.lineKey, payload: { explanation, reason } },
              { onSuccess: () => setExplaining(null) },
            );
          }}
        />
      )}
    </QueryBoundary>
  );
}

function ExplainLineDialog({
  line,
  isSaving,
  onClose,
  onSubmit,
}: {
  line: IcaapRequirementLine;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (explanation: string, reason: string) => void;
}) {
  const [explanation, setExplanation] = useState(line.explanation ?? "");
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title={`Why ${line.label} differs`}
      description={`Pillar 1 ${fmtAmount(line.regulatoryAmount)} against the ICAAP's ${fmtAmount(line.internalAmount)}.`}
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
        <FieldLabel label="Explanation">
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
