"use client";

/**
 * The stress results and the capital plan, as this ICAAP holds them.
 *
 * THE TAB IS READ-ONLY OVER BOUND EVIDENCE. It shows the figures the cycle is
 * bound to — not the newest stress run in the platform. An ICAAP that rests on
 * an attested run from March must not be illustrated with a run from June: the
 * whole point of binding evidence to a cycle is that the assessment and its
 * evidence cannot drift apart. Where the binding is stale, the strip says so in
 * the same words the Sections tab uses.
 *
 * NOTHING HERE IS RE-IMPLEMENTED. Appendix II, the management-action
 * comparison, the Board attestation and the capital plan's projection are the
 * platform's existing components, given this cycle's own run. A second
 * rendering of a regulatory table is a second opinion about what it says.
 */

import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import AppendixIITables from "@/components/stress/AppendixIITables";
import ManagementActionsPanel from "@/components/stress/ManagementActionsPanel";
import SignoffPanel from "@/components/stress/SignoffPanel";
import { useEnterpriseStressRun } from "@/components/stress/hooks";
import type { AppendixIITables as AppendixTables } from "@/components/stress/types";
import CapitalPlanProjection, {
  CapitalPlanProjectionUnavailable,
} from "@/components/basel/CapitalPlanProjection";
import { useCapitalPlan } from "@/lib/api/hooks";
import { useIcaapStressEvidence } from "@/lib/api/icaapRiskCapital";
import type { IcaapStressEvidenceBlock } from "@/lib/api/icaapRiskCapital";
import { blockStatusCopy, fmtDateValue, fmtFact } from "../format";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import { NOT_AVAILABLE } from "./labels";

const TONE_CLASS: Record<string, string> = {
  ok: "text-success",
  warn: "text-warning",
  crit: "text-critical",
  neutral: "text-slate",
};

export default function StressCapitalPlan({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const evidenceQuery = useIcaapStressEvidence(bankId, cycleId);
  const evidence = evidenceQuery.data;
  const runQuery = useEnterpriseStressRun(bankId, evidence?.runId ?? null);
  const planQuery = useCapitalPlan(bankId);

  const unavailable = p2UnavailableNotice(evidenceQuery.error);
  if (unavailable) {
    return (
      <P2Unavailable title="Stress and capital plan" message={unavailable} />
    );
  }

  const blocks = evidence?.blocks ?? [];
  const appendix = evidence?.appendix ?? null;
  const run = runQuery.data ?? null;
  const plan = planQuery.data;

  return (
    <QueryBoundary
      isLoading={evidenceQuery.isLoading}
      error={evidenceQuery.error}
      onRetry={() => void evidenceQuery.refetch()}
      contained
    >
      {evidence && (
        <div className="space-y-4">
          <SectionCard
            title="What this assessment's stress results rest on"
            subtitle="The figures bound into this cycle. Linking, refreshing and pinning them is done on the Sections tab."
            noPadding
          >
            {blocks.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="No stress or capital-plan figures are linked yet"
                  description="Link the attested stress run, its narratives, the management-action plan and the approved capital plan on the Sections tab. They are then shown here exactly as the assessment holds them."
                />
              </div>
            ) : (
              <ul className="divide-y divide-border-light">
                {blocks.map((block) => (
                  <EvidenceRow key={block.blockId} block={block} />
                ))}
              </ul>
            )}
          </SectionCard>

          {appendix === null ? (
            <SectionCard
              title="Appendix II — regulatory deliverable"
              subtitle="The stress submission tables."
            >
              <p className="text-body text-slate">
                {evidence.runId === null
                  ? "The attested stress run is not linked to this cycle, so its Appendix II tables cannot be shown."
                  : "The linked stress run does not carry a complete set of Appendix II tables. Re-run the enterprise stress test and attest it again, then refresh the link on the Sections tab."}
              </p>
            </SectionCard>
          ) : (
            <AppendixIITables
              tables={appendix as unknown as AppendixTables}
            />
          )}

          {run === null ? (
            <SectionCard
              title="Management actions and Board attestation"
              subtitle="Read from the stress run this cycle is bound to."
            >
              <p className="text-body text-slate">
                {evidence.runId === null
                  ? "No stress run is bound to this cycle, so there is nothing to attest here."
                  : "The stress run this cycle is bound to could not be opened. It may have been removed; the binding on the Sections tab records which run it was."}
              </p>
            </SectionCard>
          ) : (
            <>
              <ManagementActionsPanel run={run} />
              <SignoffPanel run={run} bankId={bankId} />
            </>
          )}

          {plan?.projection ? (
            <CapitalPlanProjection projection={plan.projection} />
          ) : plan?.projectionUnavailable ? (
            <CapitalPlanProjectionUnavailable
              unavailable={plan.projectionUnavailable}
            />
          ) : (
            <SectionCard
              title="Capital plan projection"
              subtitle="The plan's own five-year path."
            >
              <p className="text-body text-slate">
                The capital plan has no stored projection for this institution
                yet.
              </p>
            </SectionCard>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

function EvidenceRow({ block }: { block: IcaapStressEvidenceBlock }) {
  const status = blockStatusCopy(block.status, {
    asOf: block.sourceAsOf ? fmtDateValue(block.sourceAsOf) : undefined,
    pinReason: block.pinReason,
  });
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-medium text-navy">{block.title}</span>
        <span className={`text-caption ${TONE_CLASS[status.tone]}`}>
          {status.label}
        </span>
        <span className="text-caption text-slate">
          {block.sourceAsOf
            ? `as at ${fmtDateValue(block.sourceAsOf)}`
            : "no date recorded"}
        </span>
      </div>
      {block.sourceLabel && (
        <p className="text-caption text-slate">{block.sourceLabel}</p>
      )}
      {block.statusDetail && (
        <p className="text-caption text-warning">{block.statusDetail}</p>
      )}
      {block.facts.length > 0 && (
        <dl className="mt-1 flex flex-wrap gap-x-6 gap-y-1">
          {block.facts.map((fact) => (
            <div key={fact.key}>
              <dt className="text-caption text-slate">{fact.label}</dt>
              <dd className="text-body text-navy">
                {fact.value === null ? NOT_AVAILABLE : fmtFact(fact)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}
