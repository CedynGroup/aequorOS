"use client";

/**
 * The challenge log: what the committees and the Board asked about this ICAAP,
 * and what the preparers answered.
 *
 * NOT ROUTED YET — see the note in `AuditReview.tsx`. P3 mounts both on the
 * Review & challenge tab.
 *
 * This log is the platform's evidence of Board challenge (¶45), and D-043 makes
 * it load-bearing: the Board does not e-sign the filed PDF by default, so the
 * in-platform record of what the Board asked and what it was told is a large
 * part of how the challenge is evidenced. That is why every entry is
 * APPEND-ONLY — a challenge and a response are never edited or deleted, and the
 * screen says so before someone writes one.
 *
 * The forums come from the framework on the payload, never a local list: a
 * jurisdiction with a different committee structure names its own.
 */

import { useState } from "react";
import { MessageSquarePlus } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  ICAAP_CHALLENGE_FORUMS,
  type IcaapChallengeForum,
  useIcaapChallenges,
  useRaiseIcaapChallenge,
  useRespondIcaapChallenge,
  type IcaapChallenge,
} from "@/lib/api/icaapRiskCapital";
import { ICON_SM, REASON_MAX, ROWS_LONG } from "./display";
import { forumLabel } from "./labels";
import P2Unavailable, { p2UnavailableNotice } from "./availability";

export default function ChallengeLog({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canEdit = scope.capitalEdit === true;
  const challengesQuery = useIcaapChallenges(bankId, cycleId);
  const [raising, setRaising] = useState(false);
  const [responding, setResponding] = useState<IcaapChallenge | null>(null);

  const data = challengesQuery.data;
  const challenges = data?.challenges ?? [];
  const openCount = challenges.filter((c) => c.open).length;

  const unavailable = p2UnavailableNotice(challengesQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Challenge log" message={unavailable} />;
  }

  return (
    <QueryBoundary
      isLoading={challengesQuery.isLoading}
      error={challengesQuery.error}
      onRetry={() => void challengesQuery.refetch()}
      contained
    >
      {data && (
        <SectionCard
          title="Challenge log"
          subtitle="What the committees and the Board asked, and what they were told. Entries cannot be edited or removed."
          actions={
            canEdit ? (
              <SecondaryButton onClick={() => setRaising(true)}>
                <MessageSquarePlus size={ICON_SM} aria-hidden />
                Record a challenge
              </SecondaryButton>
            ) : undefined
          }
        >
          {openCount > 0 && (
            <p className="mb-3 text-body text-navy/80">
              {openCount} challenge{openCount === 1 ? "" : "s"} still awaiting a
              response.
            </p>
          )}

          {challenges.length === 0 ? (
            <EmptyState
              title="No challenges recorded"
              description="A regulator reads the challenge log to see that the ICAAP was questioned before it was approved. Record what was asked, and the answer given."
            />
          ) : (
            <ol className="space-y-3">
              {challenges.map((challenge) => (
                <li
                  key={challenge.challengeId}
                  className="rounded border border-border-light p-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium text-navy">
                        {forumLabel(challenge.forum)} · {challenge.raisedOn ?? ""}
                      </p>
                      <p className="text-caption text-slate">
                        Raised by {challenge.raisedByName}
                        {challenge.targetRef ? ` · on ${challenge.targetRef}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusPill tone={challenge.open ? "amber" : "success"}>
                        {challenge.open ? "Awaiting response" : "Responded"}
                      </StatusPill>
                      {canEdit && (
                        <SecondaryButton
                          onClick={() => setResponding(challenge)}
                        >
                          Respond
                        </SecondaryButton>
                      )}
                    </div>
                  </div>

                  <p className="mt-2 text-body text-navy/80">
                    {challenge.challengeText}
                  </p>

                  {(challenge.responses ?? []).length > 0 && (
                    <ul className="mt-2 space-y-2 border-l-2 border-border-light pl-3">
                      {(challenge.responses ?? []).map((response) => (
                        <li key={response.responseId}>
                          <p className="text-caption text-slate">
                            {response.respondedBy} · {response.createdAt ?? ""}
                          </p>
                          <p className="text-body text-navy/80">
                            {response.responseText}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          )}
        </SectionCard>
      )}

      {raising && data && (
        <RaiseChallengeDialog
          bankId={bankId}
          cycleId={cycleId}
          forums={ICAAP_CHALLENGE_FORUMS}
          onClose={() => setRaising(false)}
        />
      )}
      {responding && (
        <RespondDialog
          bankId={bankId}
          cycleId={cycleId}
          challenge={responding}
          onClose={() => setResponding(null)}
        />
      )}
    </QueryBoundary>
  );
}

function RaiseChallengeDialog({
  bankId,
  cycleId,
  forums,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  forums: readonly string[];
  onClose: () => void;
}) {
  const mutation = useRaiseIcaapChallenge(bankId, cycleId);
  const [forum, setForum] = useState<string>(forums[0] ?? "");
  const [raisedOn, setRaisedOn] = useState("");
  const [challengeText, setChallengeText] = useState("");
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title="Record a challenge"
      description="Append-only: once recorded, a challenge cannot be edited or removed."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              forum === "" ||
              raisedOn === "" ||
              challengeText.trim() === "" ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                {
                  raisedIn: forum as IcaapChallengeForum,
                  // The wire takes a Date; the form holds the ISO day the
                  // operator picked, which is parsed here and nowhere else.
                  raisedOn: new Date(`${raisedOn}T00:00:00Z`),
                  raisedByName: reason.trim(),
                  challengeText: challengeText.trim(),
                  severity: "medium",
                  targetKind: "cycle",
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Recording…" : "Record challenge"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}
        <FieldLabel label="Forum">
          <select
            aria-label="Forum"
            value={forum}
            onChange={(event) => setForum(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          >
            {forums.map((entry) => (
              <option key={entry} value={entry}>
                {forumLabel(entry)}
              </option>
            ))}
          </select>
        </FieldLabel>
        <FieldLabel label="Date raised">
          <input
            type="date"
            value={raisedOn}
            onChange={(event) => setRaisedOn(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="What was asked">
          <textarea
            value={challengeText}
            rows={ROWS_LONG}
            onChange={(event) => setChallengeText(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Who raised it" hint="The person or committee named in the minutes.">
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

function RespondDialog({
  bankId,
  cycleId,
  challenge,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  challenge: IcaapChallenge;
  onClose: () => void;
}) {
  const mutation = useRespondIcaapChallenge(bankId, cycleId);
  const [responseText, setResponseText] = useState("");
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title={`Respond to challenge ${challenge.challengeNo}`}
      description={challenge.challengeText}
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              responseText.trim() === "" ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                {
                  challengeId: challenge.challengeId,
                  payload: {
                    responseText: responseText.trim(),
                    responderFunction: reason.trim(),
                    outcome: "accepted_no_change",
                  },
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Recording…" : "Record response"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}
        <FieldLabel label="Response">
          <textarea
            value={responseText}
            rows={ROWS_LONG}
            onChange={(event) => setResponseText(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Who raised it" hint="The person or committee named in the minutes.">
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
