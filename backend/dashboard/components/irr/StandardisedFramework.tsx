"use client";

/**
 * The IRRBB Standardised Framework workspace.
 *
 * What this screen is careful about, in the order a reader meets it:
 *
 * 1. **It is not a return.** The framework's figures are supervisory
 *    monitoring; no return is generated from a framework run, and the only
 *    route by which one of these numbers reaches a filing is the ICAAP Pillar
 *    2 capital requirement. The standing sentence says so at the top, so
 *    nobody looks for a submit button that must never exist.
 *
 * 2. **The mandate is accurate on both sides of a date that has not arrived.**
 *    Before the governed commencement date the interim economic-value method
 *    is the correct basis; from it, the framework is required and a report
 *    still using the interim method cannot be frozen. The date itself is
 *    seeded pending confirmation and is never presented as settled.
 *
 * 3. **A refusal is a refusal.** The result read serves only the newest
 *    SUCCEEDED run, so a refused attempt would otherwise be invisible and the
 *    screen would say "nobody has run it". The attempt history is read beside
 *    it, and a refusal replaces the figures — never an understated measure,
 *    never a blank.
 *
 * 4. **Nothing absent is rendered as a number.** Every figure arrives as text
 *    or as null and is printed through `figures.ts`, which has no `?? 0`.
 *
 * Reads and mutations go through the generated client via `lib/api/irrbbSf.ts`.
 * The result is normalised before anything renders, and the 404/405/501 path
 * is decided by `sf/availability.tsx` before any payload is read.
 */

import { useState } from "react";
import { Play } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { useBankContext } from "@/components/shell/BankContext";
import { IRRBB_CONFIDENTIAL_RUN_REASON } from "@/lib/modules";
import { shortId } from "@/lib/api/values";
import {
  useIrrbbStandardisedFramework,
  useIrrbbStandardisedFrameworkAttempts,
  useRunIrrbbStandardisedFramework,
} from "@/lib/api/irrbbSf";
import SfUnavailable, { sfNotice } from "./sf/availability";
import MandateCard from "./sf/MandateCard";
import RefusalCard from "./sf/RefusalCard";
import MeasuresPanel from "./sf/MeasuresPanel";
import ScenariosPanel from "./sf/ScenariosPanel";
import BookPanel from "./sf/BookPanel";
import AssumptionsPanel from "./sf/AssumptionsPanel";
import { DIGEST_CHARS, ICON_SM } from "./sf/display";
import { rows } from "./sf/safe";
import {
  IMMUTABLE_RUN,
  NO_RESULT_ASK_COLLEAGUE,
  NO_RESULT_RUN_IT,
  PAGE_EYEBROW,
  PAGE_TITLE,
  POST_SHOCK_FLOOR_HEADING,
  RUN_ACTION,
  RUN_ACTION_PENDING,
  RUN_FAILED,
  RUN_NEEDS_AUTHORITY,
  RUN_NEEDS_PERIOD,
  SUPERVISORY_MONITORING,
  VIEW_NEEDS_AUTHORITY,
  VIEW_NEEDS_AUTHORITY_TITLE,
  runStatusCopy,
} from "./sf/labels";

export default function StandardisedFramework() {
  const { bank, period, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const canView = moduleScope.irrbbAggregatedView === true;
  const canRun = moduleScope.irrbbRun === true;

  const result = useIrrbbStandardisedFramework(
    canView ? bankId : undefined,
    periodId,
  );
  const attempts = useIrrbbStandardisedFrameworkAttempts(
    canView ? bankId : undefined,
    periodId,
  );
  const run = useRunIrrbbStandardisedFramework(bankId);
  const [runError, setRunError] = useState<string | null>(null);

  const notice = sfNotice(result.error);
  const view = result.data;
  const refusal = attempts.data?.refusal ?? null;
  const latestAttempt = attempts.data?.latest ?? null;
  /**
   * A refusal is shown when there is no result at all, and ALSO when the
   * newest attempt refused while an older result is still being served. The
   * second case is the quiet one: figures on screen that the latest run did
   * not reproduce.
   */
  const supersededByRefusal =
    refusal !== null &&
    latestAttempt !== null &&
    latestAttempt.status === "failed" &&
    (view === undefined || latestAttempt.id !== (view.run?.id ?? null));

  const startRun = async () => {
    if (!periodId || !canRun) return;
    setRunError(null);
    try {
      await run.mutateAsync({ reportingPeriodId: periodId });
    } catch {
      setRunError(RUN_FAILED);
    }
  };

  const runButton = (descriptionId?: string) => (
    <button
      type="button"
      className="btn-primary inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium disabled:cursor-not-allowed disabled:opacity-50"
      disabled={!canRun || !periodId || run.isPending}
      aria-describedby={descriptionId}
      onClick={() => void startRun()}
    >
      <Play size={ICON_SM} aria-hidden />
      {run.isPending ? RUN_ACTION_PENDING : RUN_ACTION}
    </button>
  );

  return (
    <>
      <PageHeader
        eyebrow={PAGE_EYEBROW}
        title={PAGE_TITLE}
        subtitle={SUPERVISORY_MONITORING}
        action={
          canRun ? (
            runButton()
          ) : (
            <DisabledWithReason reason={IRRBB_CONFIDENTIAL_RUN_REASON}>
              {(descriptionId) => runButton(descriptionId)}
            </DisabledWithReason>
          )
        }
      />

      <PageContainer className="space-y-6 py-6">
        {runError === null ? null : (
          <p className="text-caption text-critical" role="alert">
            {runError}
          </p>
        )}
        {periodId ? null : (
          <p className="text-caption text-slate">{RUN_NEEDS_PERIOD}</p>
        )}

        {canView ? null : (
          <SfUnavailable
            title={VIEW_NEEDS_AUTHORITY_TITLE}
            message={VIEW_NEEDS_AUTHORITY}
          />
        )}

        {supersededByRefusal && refusal !== null ? (
          <RefusalCard run={refusal} />
        ) : null}

        {notice === null ? null : (
          <SfUnavailable title={notice.title} message={notice.message}>
            {notice.kind === "no-result" && !supersededByRefusal ? (
              <p className="mt-2 text-body leading-relaxed text-navy/80">
                {canRun ? NO_RESULT_RUN_IT : NO_RESULT_ASK_COLLEAGUE}
              </p>
            ) : null}
            {notice.kind === "no-result" && !canRun ? (
              <p className="mt-2 text-caption text-slate">
                {RUN_NEEDS_AUTHORITY}
              </p>
            ) : null}
          </SfUnavailable>
        )}

        {notice === null && canView ? (
          <QueryBoundary
            isLoading={result.isLoading}
            error={result.error}
            onRetry={() => result.refetch()}
          >
            {view ? (
              <div className="space-y-6">
                <MandateCard mandate={view.mandate} />
                <MeasuresPanel view={view} />

                {!view.automaticOptionStatement &&
                !view.postShockFloorStatement &&
                rows(view.statements).length === 0 ? null : (
                  <SectionCard
                    title={POST_SHOCK_FLOOR_HEADING}
                    subtitle={view.postShockFloorStatement ?? undefined}
                  >
                    {!view.automaticOptionStatement ? null : (
                      <p className="text-body leading-relaxed text-navy">
                        {view.automaticOptionStatement}
                      </p>
                    )}
                    {rows(view.statements).length === 0 ? null : (
                      <ul className="mt-2 space-y-1">
                        {rows(view.statements).map((statement, index) => (
                          <li
                            key={index}
                            className="text-body leading-relaxed text-navy/80"
                          >
                            {statement}
                          </li>
                        ))}
                      </ul>
                    )}
                  </SectionCard>
                )}

                <ScenariosPanel view={view} />
                <BookPanel view={view} />
                <AssumptionsPanel view={view} />

                <p className="flex flex-wrap items-center gap-2 text-caption text-slate">
                  <StatusPill tone={runStatusCopy(view.run?.status ?? null).tone}>
                    {runStatusCopy(view.run?.status ?? null).label}
                  </StatusPill>
                  {view.run?.inputHash ? (
                    <span className="tabular-nums">
                      {shortId(view.run.inputHash, DIGEST_CHARS)}
                    </span>
                  ) : null}
                  <span>{IMMUTABLE_RUN}</span>
                </p>
              </div>
            ) : null}
          </QueryBoundary>
        ) : null}
      </PageContainer>
    </>
  );
}
