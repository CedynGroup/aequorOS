"use client";

/**
 * The institution's own review chain for its ICAAP.
 *
 * Every regime prescribes that the assessment is challenged and approved
 * before it is filed; none of them prescribes an institution's committee
 * structure. So the chain is the bank's to set, within rules the platform can
 * actually run: the first stage prepares, the last stage signs, at least one
 * stage between them reviews or approves, and exactly one approval stage
 * seals the report — immediately before the signature stage.
 *
 * Those rules are the SERVER's (`app/domain/icaap/workflow.py` validates the
 * framework's default and the bank's proposal with the same function), so a
 * chain this screen would accept and the server would refuse cannot exist.
 * The composer (`ChainBuilder`) therefore mirrors none of them: it builds
 * stages, the propose and update endpoints run `validate_stages` before
 * anything is written, and a refusal is printed in the server's own words.
 *
 * A chain change is maker-checker: whoever proposes it cannot approve it, and
 * a cycle already under review keeps the chain it was submitted under.
 */

import { useState } from "react";
import { Pencil, Plus } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import P2Unavailable, {
  p2UnavailableNotice,
} from "@/components/icaap/p2/availability";
import { useModuleScope } from "@/components/shell/BankContext";
import {
  ICON_XS,
  RATIONALE_MAX,
  REASON_MIN,
  ROWS_LONG,
} from "@/components/icaap/p2/display";
import {
  useDecideIcaapWorkflowTemplate,
  useIcaapWorkflowTemplates,
  useProposeIcaapWorkflowTemplate,
  useSubmitIcaapWorkflowTemplate,
  useUpdateIcaapWorkflowTemplate,
  type IcaapStageInput,
} from "@/lib/api/icaapFiling";
import ChainBuilder from "./ChainBuilder";
import {
  BUILDER_COMPOSE_TITLE,
  BUILDER_EDIT_DRAFT,
  BUILDER_EDIT_TITLE,
  BUILDER_ONE_AT_A_TIME,
  BUILDER_START_FROM_EFFECTIVE,
  chainSourceSentence,
  stageKindAsk,
  stageKindLabel,
  templateStatusCopy,
} from "./labels";

/**
 * What a chain has to satisfy, said before anybody composes one.
 *
 * This is DESCRIPTION, not validation: nothing here is evaluated, and the
 * server refuses an invalid chain in its own words. Kept because a maker
 * should know the shape before they start, not only after a refusal.
 */
const CHAIN_RULES = [
  "The first stage is preparation, and the last stage is the signature on the report.",
  "At least one stage between them reads and challenges the assessment.",
  "Exactly one approval stage seals the report, and it is the one immediately before the signature.",
  "An officer who wrote part of the assessment cannot review or approve it.",
];

function StageList({ stages }: { stages: readonly IcaapStageInput[] }) {
  return (
    <ol className="space-y-2">
      {(stages ?? []).map((stage) => (
        <li key={stage.seq} className="rounded border border-border-light p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium text-navy">{stage.title}</p>
              <p className="text-caption text-slate">
                {stageKindLabel(stage.kind)} · {stageKindAsk(stage.kind)}
              </p>
              {stage.officerTitles.length > 0 && (
                <p className="mt-1 text-caption text-slate">
                  Taken by: {stage.officerTitles.join(", ")}
                </p>
              )}
            </div>
            {stage.freezeOnApprove && (
              <StatusPill tone="slate">Seals the report</StatusPill>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function WorkflowTemplates({ bankId }: { bankId: string }) {
  const scope = useModuleScope();
  const query = useIcaapWorkflowTemplates(bankId);
  const submit = useSubmitIcaapWorkflowTemplate(bankId);
  const decide = useDecideIcaapWorkflowTemplate(bankId);
  const propose = useProposeIcaapWorkflowTemplate(bankId);
  const revise = useUpdateIcaapWorkflowTemplate(bankId);
  const [acting, setActing] = useState<
    | { kind: "submit"; templateId: string }
    | { kind: "approved" | "rejected"; templateId: string }
    | null
  >(null);
  const [reason, setReason] = useState("");
  // `templateId: null` composes a new proposal; otherwise it rewrites that
  // draft's stages. Both land on an endpoint that validates before it writes.
  const [composing, setComposing] = useState<{
    templateId: string | null;
    seed: readonly IcaapStageInput[];
    reason: string;
  } | null>(null);

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Review chain" message={unavailable} />;
  }

  const canEdit = scope.capitalEdit === true;
  const canApprove = scope.capitalApprove === true;
  const data = query.data;
  // The server admits one open proposal per institution and answers
  // `workflow_template_open` otherwise, so the screen offers editing that one
  // rather than a second compose the server would refuse.
  const openProposal = (data?.templates ?? []).find(
    (template) =>
      template.status === "draft" || template.status === "pending_approval",
  );
  const saving = propose.isPending || revise.isPending;
  const saveError = propose.isError
    ? (propose.error as Error).message
    : revise.isError
      ? (revise.error as Error).message
      : null;

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {data && (
        <div className="space-y-4">
          <SectionCard
            title="The chain in force"
            subtitle={`A cycle put forward today would follow ${chainSourceSentence(data.effectiveSource)}.`}
            actions={
              canEdit ? (
                <SecondaryButton
                  disabled={openProposal !== undefined}
                  title={
                    openProposal === undefined
                      ? undefined
                      : BUILDER_ONE_AT_A_TIME
                  }
                  onClick={() =>
                    setComposing({
                      templateId: null,
                      seed: data.effectiveStages ?? [],
                      reason: "",
                    })
                  }
                >
                  <Plus size={ICON_XS} aria-hidden />
                  {BUILDER_START_FROM_EFFECTIVE}
                </SecondaryButton>
              ) : undefined
            }
          >
            {(data.effectiveStages ?? []).length === 0 ? (
              <EmptyState
                title="No chain is set"
                description="The regime's own default chain applies until this institution approves its own."
              />
            ) : (
              <StageList stages={data.effectiveStages ?? []} />
            )}
            <div className="mt-4">
              <p className="text-caption font-medium text-navy">
                What a chain has to satisfy
              </p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-caption text-slate">
                {CHAIN_RULES.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </div>
          </SectionCard>

          <SectionCard
            title="Proposed and past chains"
            subtitle="A change is proposed by one officer and approved by another. A cycle already under review keeps the chain it was put forward under."
          >
            {(data.templates ?? []).length === 0 ? (
              <EmptyState
                title="Nothing proposed"
                description="Until this institution proposes its own chain, the regime's default is used. Compose one from the chain in force above."
              />
            ) : (
              <ul className="space-y-3">
                {(data.templates ?? []).map((template) => {
                  const status = templateStatusCopy(template.status);
                  return (
                    <li
                      key={template.id}
                      className="rounded border border-border-light p-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="font-medium text-navy">
                            Version {template.version ?? ""}
                          </p>
                          <p className="text-caption text-slate">
                            {template.reason}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill tone={status.tone}>
                            {status.label}
                          </StatusPill>
                          {canEdit && template.status === "draft" && (
                            <>
                              <SecondaryButton
                                onClick={() =>
                                  setComposing({
                                    templateId: template.id,
                                    seed: template.stages ?? [],
                                    reason: template.reason,
                                  })
                                }
                              >
                                <Pencil size={ICON_XS} aria-hidden />
                                {BUILDER_EDIT_DRAFT}
                              </SecondaryButton>
                              <SecondaryButton
                                onClick={() => {
                                  setReason("");
                                  setActing({
                                    kind: "submit",
                                    templateId: template.id,
                                  });
                                }}
                              >
                                Put forward for approval
                              </SecondaryButton>
                            </>
                          )}
                          {canApprove &&
                            template.status === "pending_approval" && (
                              <>
                                <PrimaryButton
                                  onClick={() => {
                                    setReason("");
                                    setActing({
                                      kind: "approved",
                                      templateId: template.id,
                                    });
                                  }}
                                >
                                  Approve
                                </PrimaryButton>
                                <SecondaryButton
                                  onClick={() => {
                                    setReason("");
                                    setActing({
                                      kind: "rejected",
                                      templateId: template.id,
                                    });
                                  }}
                                >
                                  Do not approve
                                </SecondaryButton>
                              </>
                            )}
                        </div>
                      </div>
                      <div className="mt-2">
                        <StageList stages={template.stages ?? []} />
                      </div>
                      {template.decisionReason && (
                        <p className="mt-2 text-caption text-slate">
                          {template.decisionReason}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </SectionCard>
        </div>
      )}

      {composing !== null && (
        <ChainBuilder
          title={
            composing.templateId === null
              ? BUILDER_COMPOSE_TITLE
              : BUILDER_EDIT_TITLE
          }
          seed={composing.seed}
          initialReason={composing.reason}
          saving={saving}
          error={saveError}
          onClose={() => {
            propose.reset();
            revise.reset();
            setComposing(null);
          }}
          onSave={(payload) => {
            const onSuccess = () => setComposing(null);
            if (composing.templateId === null) {
              propose.mutate(payload, { onSuccess });
              return;
            }
            revise.mutate(
              { ...payload, templateId: composing.templateId },
              { onSuccess },
            );
          }}
        />
      )}

      {acting !== null && (
        <Dialog
          title={
            acting.kind === "submit"
              ? "Put this chain forward for approval"
              : acting.kind === "approved"
                ? "Approve this chain"
                : "Do not approve this chain"
          }
          description="Recorded in the audit trail with your name."
          onClose={() => setActing(null)}
          footer={
            <>
              <SecondaryButton onClick={() => setActing(null)}>
                Cancel
              </SecondaryButton>
              <PrimaryButton
                disabled={
                  submit.isPending ||
                  decide.isPending ||
                  reason.trim().length < REASON_MIN
                }
                onClick={() => {
                  const onSuccess = () => setActing(null);
                  if (acting.kind === "submit") {
                    submit.mutate(
                      { templateId: acting.templateId, reason: reason.trim() },
                      { onSuccess },
                    );
                    return;
                  }
                  decide.mutate(
                    {
                      templateId: acting.templateId,
                      decision: acting.kind,
                      reason: reason.trim(),
                    },
                    { onSuccess },
                  );
                }}
              >
                {submit.isPending || decide.isPending ? "Saving…" : "Confirm"}
              </PrimaryButton>
            </>
          }
        >
          <div className="space-y-3">
            {(submit.isError || decide.isError) && (
              <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
                {
                  ((submit.isError ? submit.error : decide.error) as Error)
                    .message
                }
              </p>
            )}
            <FieldLabel label="Reason">
              <textarea
                value={reason}
                rows={ROWS_LONG}
                maxLength={RATIONALE_MAX}
                onChange={(event) => setReason(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </div>
        </Dialog>
      )}
    </QueryBoundary>
  );
}
