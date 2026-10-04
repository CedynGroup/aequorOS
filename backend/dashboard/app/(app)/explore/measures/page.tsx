"use client";

/**
 * Calculated measures — the figures this institution defined for itself.
 *
 * A calculated measure is a formula over figures the catalogue already publishes,
 * and it is the one place in the analytics workspace where a bank adds to the
 * vocabulary rather than reading it. That makes it governed: a draft is one
 * person's, a certified measure is the institution's, and getting from one to the
 * other takes two people.
 *
 * WHAT THIS PAGE WILL NOT DO, each for a reason that is not convenience:
 *
 * * It will not decide whether a formula is valid. `POST …/bi/measures/validation`
 *   is the authority and its verdict is rendered as the server worded it. A
 *   TypeScript parser that disagreed with `app/domain/bi/expr.py` would be worse
 *   than no preview at all.
 * * It will not offer a decision control to the person who proposed a measure, and
 *   it says why rather than quietly leaving the button out.
 * * It will not offer to delete a certified measure. The route permits it — owner
 *   only, unconditional, in every state — and that is a governance hole rather
 *   than a feature: two people certify a formula and one could destroy it, along
 *   with the approved text that is the record of what they agreed. The control is
 *   withheld with the reason on screen; see `components/bi/measures.ts`.
 * * It will not name a measure this reader may not read. A measure whose figures
 *   their access does not cover is ABSENT from the list, because its name is
 *   authored text that can describe the very figure they were refused. Nothing
 *   here counts them, states a total or hints that the list is partial — the
 *   server does not say, and inventing the hint is the disclosure.
 */

import { useMemo, useState } from "react";
import { FunctionSquare, Pencil, Plus, Trash2 } from "lucide-react";
import type { BiMeasureRead } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import MeasureComposer from "@/components/bi/MeasureComposer";
import MeasureReview, {
  type MeasureDecision,
} from "@/components/bi/MeasureReview";
import type { ExpressionVerdict } from "@/components/bi/ExpressionEditor";
import { grantSentence } from "@/components/bi/labels";
import {
  DigestUnavailable,
  directionLabel,
  measureControls,
  measureStanding,
  refusedFigures,
  refusedFiguresSentence,
  valueTypeLabel,
  type MeasureDraft,
} from "@/components/bi/measures";
import {
  isBiUnavailable,
  useBiCatalogue,
  useBiMeasures,
  useCreateBiMeasure,
  useDecideBiMeasure,
  useDeleteBiMeasure,
  useProposeBiMeasure,
  useUpdateBiMeasure,
  useValidateBiMeasureExpression,
} from "@/lib/api/bi";
import { isApiError } from "@/lib/api/client";
import { sodFindings, sodRemedy } from "@/lib/api/sodDecision";

/**
 * Whatever a refused mutation should say, in the reader's words.
 *
 * THE FIGURE IS THE SUBJECT OF A 403, NEVER THE FORMULA. A calculated measure is
 * authorized as the figures its text names, so "your access does not cover this
 * measure" would be both untrue and unactionable; the server sends the figures and
 * their labels, and those are what the reader takes to an Org Owner. Everything
 * else falls back to the server's own message, which is production copy.
 */
function refusalSentence(error: unknown): string | null {
  if (error === null || error === undefined) return null;
  if (error instanceof DigestUnavailable) return error.message;
  if (isApiError(error)) {
    return refusedFiguresSentence(refusedFigures(error.details)) ?? error.message;
  }
  return error instanceof Error
    ? error.message
    : "That could not be saved. Try again.";
}

export default function BiMeasuresPage() {
  const { bank } = useBankContext();
  const measures = useBiMeasures(bank?.id);
  const catalogue = useBiCatalogue(bank?.id);

  const validate = useValidateBiMeasureExpression(bank?.id);
  const create = useCreateBiMeasure(bank?.id);
  const update = useUpdateBiMeasure(bank?.id);
  const remove = useDeleteBiMeasure(bank?.id);
  const propose = useProposeBiMeasure(bank?.id);
  const decide = useDecideBiMeasure(bank?.id);

  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState<BiMeasureRead | null>(null);
  const [verdict, setVerdict] = useState<ExpressionVerdict | null>(null);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const rows = useMemo(() => measures.data?.measures ?? [], [measures.data]);
  const figures = catalogue.data?.measures ?? [];

  function resetComposer(): void {
    setComposing(false);
    setEditing(null);
    setVerdict(null);
    create.reset();
    update.reset();
    validate.reset();
  }

  function check(expression: string): void {
    validate.mutate(expression, {
      onSuccess: (read) => setVerdict({ expression, read }),
    });
  }

  function submit(draft: MeasureDraft): void {
    const body = {
      label: draft.label.trim(),
      description: draft.description.trim(),
      expression: draft.expression,
      valueType: draft.valueType,
      favourableDirection: draft.favourableDirection,
    };
    if (editing) {
      update.mutate(
        { measureId: editing.id, body },
        { onSuccess: resetComposer },
      );
      return;
    }
    create.mutate(
      { ...body, measureKey: draft.measureKey.trim() },
      { onSuccess: resetComposer },
    );
  }

  function sendForReview(measure: BiMeasureRead, reason: string): void {
    setActing(measure.id);
    propose.mutate({ measureId: measure.id, reason });
  }

  function takeDecision(
    measure: BiMeasureRead,
    decision: MeasureDecision,
    reason: string,
  ): void {
    setActing(measure.id);
    decide.mutate({
      measureId: measure.id,
      reviewedExpression: measure.expression,
      decision,
      reason,
    });
  }

  if (isBiUnavailable(measures.error)) {
    return (
      <>
        <PageHeader title="Calculated measures" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={FunctionSquare}
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace, so there is nothing to write a formula over. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  const savingComposer = create.isPending || update.isPending;
  const composerError = refusalSentence(create.error ?? update.error);

  return (
    <>
      <PageHeader
        title="Calculated measures"
        subtitle="Figures this institution worked out for itself, from figures the platform already publishes. Only formulas over figures your own access covers are shown."
        action={
          // A formula is written over one institution's figures, and every
          // check and save is addressed to it — so nothing is offered until
          // the institution is known, rather than a check that goes nowhere.
          !composing &&
          bank && (
            <button
              type="button"
              onClick={() => {
                setEditing(null);
                setVerdict(null);
                create.reset();
                update.reset();
                setComposing(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white"
            >
              <Plus size={13} aria-hidden />
              New measure
            </button>
          )
        }
      />

      <PageContainer className="flex flex-col gap-6 py-6">
        {measures.data && (
          <p className="text-caption text-slate">{measures.data.message}</p>
        )}

        {composing && (
          <SectionCard
            title={editing ? "Change this formula" : "A new calculated measure"}
            subtitle="Only figures your own access covers can be named, and the server decides whether the formula is one."
          >
            <MeasureComposer
              editing={editing}
              figures={figures}
              verdict={verdict}
              checking={validate.isPending}
              checkError={refusalSentence(validate.error)}
              onCheck={check}
              saving={savingComposer}
              saveError={composerError}
              onSubmit={submit}
              onCancel={resetComposer}
            />
          </SectionCard>
        )}

        {measures.isError && !isBiUnavailable(measures.error) && (
          <ErrorPanel
            error={measures.error}
            title="Could not read this institution's calculated measures"
            onRetry={() => void measures.refetch()}
          />
        )}

        {measures.isPending ? (
          <p className="text-caption text-slate">Reading the formulas…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            Icon={FunctionSquare}
            title="No formulas yet"
            description={`A calculated measure is a formula over figures the catalogue already publishes — a ratio, a movement against the period before, or a figure that only makes sense to this institution. Write one, and an approver can certify it for everyone whose access covers the figures it reads. If you expected to see somebody else's, an Org Owner can grant the figures behind it — for example ${grantSentence(
              "credit",
              "aggregated",
            )}.`}
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((measure) => {
              const standing = measureStanding(measure.state);
              const controls = measureControls(measure);
              const busy = acting === measure.id;
              return (
                <li key={measure.id}>
                  <SectionCard
                    title={
                      <span className="flex flex-wrap items-center gap-2">
                        {measure.label}
                        <StatusPill tone={standing.tone}>
                          {standing.label}
                        </StatusPill>
                        {!measure.ownedByCaller && (
                          <span className="text-micro font-normal text-slate">
                            Written by{" "}
                            {measure.ownerDisplayName ?? "another colleague"}
                          </span>
                        )}
                      </span>
                    }
                    subtitle={
                      <>
                        <span className="font-mono">{measure.measureKey}</span> ·{" "}
                        {valueTypeLabel(measure.valueType)} ·{" "}
                        {directionLabel(measure.favourableDirection)}
                      </>
                    }
                    actions={
                      <div className="flex items-center gap-1.5">
                        {controls.canEdit && (
                          <button
                            type="button"
                            onClick={() => {
                              setEditing(measure);
                              setVerdict(null);
                              create.reset();
                              update.reset();
                              validate.reset();
                              setComposing(true);
                            }}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-slate hover:bg-surface"
                          >
                            <Pencil size={12} aria-hidden />
                            Change
                          </button>
                        )}
                        {controls.canDelete &&
                          (armedDelete === measure.id ? (
                            <>
                              <button
                                type="button"
                                disabled={remove.isPending}
                                onClick={() => {
                                  setArmedDelete(null);
                                  remove.mutate(measure.id);
                                }}
                                className="rounded-md border border-critical/30 bg-critical-light px-2 py-1 text-caption font-medium text-critical disabled:opacity-50"
                              >
                                Delete it
                              </button>
                              <button
                                type="button"
                                onClick={() => setArmedDelete(null)}
                                className="rounded-md border border-border px-2 py-1 text-caption text-slate hover:bg-surface"
                              >
                                Keep it
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setArmedDelete(measure.id)}
                              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-critical hover:bg-critical-light"
                            >
                              <Trash2 size={12} aria-hidden />
                              Delete
                            </button>
                          ))}
                      </div>
                    }
                  >
                    <p className="font-mono text-micro leading-relaxed text-navy">
                      {measure.expression}
                    </p>
                    {measure.description.length > 0 && (
                      <p className="mt-2 text-body leading-relaxed text-navy">
                        {measure.description}
                      </p>
                    )}
                    <p className="mt-2 text-caption text-slate">
                      Reads {measure.referencedMemberLabels.join(", ")}.
                    </p>
                    <p className="mt-1 text-caption text-slate">
                      {standing.description}
                    </p>
                    {controls.deleteWithheld !== null && (
                      <p className="mt-2 rounded-md border border-border bg-surface px-3 py-2 text-caption text-navy">
                        {controls.deleteWithheld}
                      </p>
                    )}
                    <div className="mt-3 border-t border-border-light pt-3">
                      <MeasureReview
                        measure={measure}
                        proposing={busy && propose.isPending}
                        deciding={busy && decide.isPending}
                        refusal={
                          busy
                            ? refusalSentence(propose.error ?? decide.error)
                            : null
                        }
                        sodFindings={
                          busy && isApiError(decide.error)
                            ? sodFindings(decide.error.details)
                            : []
                        }
                        sodRemedy={
                          busy && isApiError(decide.error)
                            ? sodRemedy(sodFindings(decide.error.details))
                            : null
                        }
                        onPropose={(reason) => sendForReview(measure, reason)}
                        onDecide={(decision, reason) =>
                          takeDecision(measure, decision, reason)
                        }
                      />
                    </div>
                  </SectionCard>
                </li>
              );
            })}
          </ul>
        )}

        {remove.isError && (
          <ErrorPanel
            error={remove.error}
            title="Could not delete that formula"
          />
        )}
      </PageContainer>
    </>
  );
}
