"use client";

/**
 * Ask a question in words, see what the platform understood, then run it.
 *
 * `docs/bi.md` §Phase 5: "the model emits a `BiQuery` restricted to the members
 * the user can see, never SQL. It's shown to the user for confirmation and
 * logged." The backend enforces all four clauses; this page exists so the third
 * one is true of a PERSON and not only of an API contract.
 *
 * The order on screen is the order of the guarantees, and none of the steps may
 * be collapsed:
 *
 *   1. the reader types a question AND chooses the reporting date — the platform
 *      never lets a model name a date, because a real figure for the wrong date
 *      is the most convincing wrong answer available here;
 *   2. the platform proposes a query and the page shows it back as a checklist in
 *      the catalogue's own words, having read nothing;
 *   3. the reader confirms, and the proposal is echoed back UNMODIFIED — the
 *      server refuses anything whose digest differs, which is what makes the
 *      confirmation its property rather than this page's promise;
 *   4. the answer is the same `BiQueryResult` any other BI read returns, under
 *      the reader's own authority, with the same reconciliation badge.
 *
 * WHY THERE IS NO "EDIT THE QUERY" CONTROL. The only two things a reader can do
 * with a proposal are run it or discard it. Offering a field editor would produce
 * a hand-built query wearing the confirmation of a proposed one — and the server
 * would refuse it anyway. Explore is where a question is built by hand, and it is
 * authorized and logged in its own right.
 *
 * WHY EVERY REFUSAL HERE IS THE SERVER'S SENTENCE. A refusal on this path is a
 * closed reason code with production copy attached; the page renders that copy
 * verbatim and never composes its own. One refusal deliberately covers three
 * different facts — a figure that does not exist, one this reader's grants hide,
 * and one that exists but was not offered for this question — and this page must
 * not be able to tell them apart, because being able to is the disclosure.
 */

import { useState } from "react";
import { MessageSquareQuote } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import AskAnswer from "@/components/bi/AskAnswer";
import AskConfirmation from "@/components/bi/AskConfirmation";
import AskQuestionForm from "@/components/bi/AskQuestionForm";
import { askIsPending, askRefusal } from "@/lib/api/ask";
import { useBiAsk } from "@/lib/api/askTransport";
import { isoDay } from "@/lib/api/biKeys";
import { isBiUnavailable } from "@/lib/api/bi";

export default function AskPage() {
  const { bank, period, isLoading } = useBankContext();
  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;

  const { ask, run, abandon, proposal, failure, isAsking } = useBiAsk(bank?.id);

  // The flag is read by the nav and the route guard (`ModuleScope.nlqEnabled`), so
  // a reader normally never reaches this page with the surface switched off. A
  // deep link that arrives while the flag is still resolving does, and it is
  // answered by the server's own sentence below rather than by a guess here.
  const refusal = askRefusal(failure) ?? askRefusal(run.error);
  const unexpected = refusal === null ? (failure ?? run.error ?? null) : null;

  const waiting = proposal !== null && askIsPending(proposal.state);
  const proposed = proposal?.state === "proposed" ? proposal : null;
  const stopped =
    proposal !== null &&
    (proposal.state === "refused" || proposal.state === "stopped")
      ? proposal
      : null;

  // A reader whose access covers no institution has nothing to ask ABOUT, and the
  // honest thing is to say so rather than hand them a box that will not accept a
  // question. Gated on `isLoading` so it does not flash before the bank resolves.
  if (!isLoading && !bank?.id) {
    return (
      <>
        <PageHeader title="Ask a question" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={MessageSquareQuote}
            title="No institution to ask about"
            description="Your access does not yet cover an institution's figures, so there is nothing here to ask a question about. An organization owner can grant you access to one."
          />
        </PageContainer>
      </>
    );
  }

  // Only the ASK itself can say the surface is not here. A 404 from the poll or
  // the run is "Question not found" — the proposal expired, or belongs to another
  // reader — and the question had already been accepted by then; rendering it as
  // "Business intelligence is not available here" would tell a reader the whole
  // workspace had gone. Those two fall through to the platform's own sentence.
  if (isBiUnavailable(ask.error)) {
    return (
      <>
        <PageHeader title="Ask a question" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={MessageSquareQuote}
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace, so there is nothing to ask about. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Ask a question"
        subtitle="Describe what you want to know. The platform works out which figures you meant, shows you the question it would run, and runs it only when you say so."
        asOf={asOf}
      />

      <PageContainer className="space-y-6 py-6">
        <AskQuestionForm
          asOf={asOf}
          onAsOfChange={setChosenDate}
          onAsk={(question) => {
            run.reset();
            ask.mutate({ question, asOf });
          }}
          busy={isAsking || waiting}
          waitingMessage={waiting ? proposal.message : null}
          onStopWaiting={abandon}
          disabled={!bank?.id}
        />

        {/* The platform's own sentence for a refusal. Never this page's words. */}
        {refusal !== null && (
          <div
            className="card border-l-4 border-l-border bg-surface/60 p-5"
            role="status"
          >
            <p className="text-body leading-relaxed text-navy">{refusal}</p>
            {proposal !== null && proposal.figuresOffered > 0 && (
              <p className="mt-2 text-caption text-slate">
                {proposal.figuresOffered} figures your access covers were
                considered for this question.
              </p>
            )}
          </div>
        )}

        {unexpected !== null && (
          <ErrorPanel
            error={unexpected}
            title="Could not work out that question"
          />
        )}

        {/* A question the platform declined to translate: its own message, and the
            figures it can suggest — with the PLATFORM's labels, never a model's
            words, and never the ones it would not name. */}
        {stopped !== null && (
          <div className="card space-y-3 p-5">
            <p className="text-body leading-relaxed text-navy">
              {stopped.message}
            </p>
            {stopped.suggestions.length > 0 && (
              <div>
                <p className="text-caption font-medium text-navy">
                  Figures you could ask about instead
                </p>
                <ul className="mt-1 flex flex-wrap gap-2">
                  {stopped.suggestions.map((suggestion) => (
                    <li
                      key={suggestion.memberId}
                      className="rounded-md border border-border bg-surface px-2.5 py-1 text-caption text-navy"
                    >
                      {suggestion.label}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {proposed !== null && run.data === undefined && (
          <AskConfirmation
            proposal={proposed}
            onConfirm={() => run.mutate(proposed)}
            onDiscard={abandon}
            confirming={run.isPending}
          />
        )}

        {proposed !== null && run.data !== undefined && (
          <AskAnswer proposal={proposed} result={run.data} />
        )}
      </PageContainer>
    </>
  );
}
