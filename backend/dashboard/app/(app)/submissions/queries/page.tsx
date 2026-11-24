"use client";

/**
 * Regulatory Reporting — the regulator's queries on the bank's filings.
 *
 * A supervisor's question about a filing is raised at the AequorOS Reporting
 * Gateway and lives there; this page reads it live (every minute while open) and
 * answers it. Opening a query records, at the regulator, that it reached the
 * bank, which the supervisor sees as "acknowledged". Replying is the transmitting
 * officer's act, the same authority that sends a return, and every reply is in
 * the bank's audit trail.
 *
 * A request for resubmission is answered the way the regulator asked: correct the
 * return, re-file it, and reply here naming the corrected filing's receipt. The
 * Validator or the return's preparer opens the correction.
 *
 * Each act is offered only to an officer the server would accept for it, read from
 * the same authority projection the Approvals screen uses (`filingAuthorityFor`).
 * Until that projection settles, nothing actionable renders.
 */

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, MessageSquare, Send } from "lucide-react";
import type { RegulatorQueryRead } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  useAcknowledgeRegulatorQuery,
  useRegulatorQueries,
  useRespondToRegulatorQuery,
  useStartRegulatorCorrection,
} from "@/lib/api/hooks";
import { fmtDateUTC, fmtTimestamp } from "@/lib/api/values";
import {
  type FilingAuthority,
  filingAuthorityFor,
} from "@/lib/submissions/filingAuthority";
import { centralBankName, regShort } from "@/lib/format";

type Tone = "approaching" | "slate" | "success";

function asDate(value: Date | string | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function standing(query: RegulatorQueryRead): { label: string; tone: Tone } {
  if (query.status === "closed") return { label: "Closed", tone: "success" };
  if (query.status === "awaiting_supervisor")
    return { label: `With ${regShort()}`, tone: "slate" };
  return { label: "Awaiting your reply", tone: "approaching" };
}

function overdue(query: RegulatorQueryRead, now: Date): boolean {
  const due = asDate(query.dueAt);
  return query.status === "awaiting_institution" && due !== null && due < now;
}

function QueryRow({
  query,
  selected,
  onSelect,
  now,
}: {
  query: RegulatorQueryRead;
  selected: boolean;
  onSelect: () => void;
  now: Date;
}) {
  const state = standing(query);
  const due = asDate(query.dueAt);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={`w-full text-left px-4 py-3 border-b border-border transition-colors ${
          selected ? "bg-action-light" : "hover:bg-surface"
        }`}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="font-mono text-micro text-slate">{query.number}</span>
          <StatusPill tone={state.tone}>{state.label}</StatusPill>
        </span>
        <span className="mt-1 block text-body font-medium text-navy">{query.subject}</span>
        <span className="mt-0.5 block text-caption text-slate">
          {query.filing
            ? `${query.filing.returnCode} · ${fmtDateUTC(asDate(query.filing.reportingDate) ?? new Date())}`
            : `Receipt ${query.receiptId}`}
          {due ? (
            <span className={overdue(query, now) ? "text-critical" : ""}>
              {` · reply due ${fmtDateUTC(due)}`}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function Thread({ query }: { query: RegulatorQueryRead }) {
  return (
    <ol className="flex flex-col gap-3" aria-label="Messages">
      {query.messages.map((message) => {
        const ours = message.author === "institution";
        const system = message.author === "system";
        const at = asDate(message.createdAt);
        if (system) {
          return (
            <li key={message.id} className="text-caption text-slate">
              {message.body}
              {at ? ` · ${fmtTimestamp(at)}` : ""}
            </li>
          );
        }
        return (
          <li
            key={message.id}
            className={`max-w-[85%] rounded-md border px-4 py-3 ${
              ours ? "self-end border-action/30 bg-action-light" : "self-start border-border bg-surface"
            }`}
          >
            <p className="text-caption text-slate">
              <span className="font-medium text-navy">{message.authorLabel}</span>
              {ours ? " · your bank" : ` · ${centralBankName()}`}
              {at ? ` · ${fmtTimestamp(at)}` : ""}
            </p>
            <p className="mt-1.5 whitespace-pre-wrap text-body text-navy leading-relaxed">
              {message.body}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * A request for a corrected filing is answered by correcting the return, not by
 * writing back. Starting the correction sends the return to its preparer with the
 * regulator's words as the reason; when the corrected version is transmitted, it
 * goes as an amendment and AequorOS answers this request with the new receipt.
 */
function CorrectionStep({
  bankId,
  query,
  mayStart,
}: {
  bankId: string;
  query: RegulatorQueryRead;
  mayStart: boolean;
}) {
  const start = useStartRegulatorCorrection(bankId);
  const reportingDate = query.filing
    ? (asDate(query.filing.reportingDate) ?? new Date()).toISOString().slice(0, 10)
    : null;
  const returnsHref = query.filing
    ? `/submissions/returns?code=${encodeURIComponent(query.filing.returnCode)}&date=${encodeURIComponent(reportingDate ?? "")}&corrections=1`
    : null;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-warning/30 bg-warning-light px-4 py-3">
      <p className="text-caption font-medium text-navy">
        {centralBankName()} asked for a corrected filing
        {query.filing ? ` of ${query.filing.returnCode} for ${fmtDateUTC(asDate(query.filing.reportingDate) ?? new Date())}` : ""}.
      </p>
      <ol className="list-decimal pl-5 text-caption text-navy leading-relaxed">
        <li>Start the correction: the return goes back to its preparer with this request as the reason.</li>
        <li>The preparer generates the corrected version; it is approved and validated as usual.</li>
        <li>
          The Validator transmits it. It goes as an amendment of receipt {query.receiptId}, and
          AequorOS answers this request with the new receipt.
        </li>
      </ol>
      {start.isSuccess ? (
        <p role="status" className="text-caption text-positive">
          Correction opened. The return is with its preparer
          {returnsHref ? (
            <>
              {" "}
              (
              <Link className="underline" href={returnsHref}>
                open it in Returns
              </Link>
              )
            </>
          ) : null}
          .
        </p>
      ) : query.filing && !mayStart ? (
        <p className="text-caption text-slate">
          The Validator or the preparer of {query.filing.returnCode} starts the correction. Your
          account holds neither role for this return.
        </p>
      ) : query.filing ? (
        <div>
          <button
            type="button"
            disabled={start.isPending}
            onClick={() => start.mutate(query.number)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            {start.isPending ? <Loader2 size={13} className="animate-spin" aria-hidden /> : null}
            Start the correction
          </button>
        </div>
      ) : (
        <p className="text-caption text-slate">
          Receipt {query.receiptId} is not a filing of this workspace, so there is no return here to correct.
        </p>
      )}
      {start.isError ? <ErrorPanel error={start.error} title="The correction was not opened" /> : null}
    </div>
  );
}

function Reply({
  bankId,
  query,
  authority,
}: {
  bankId: string;
  query: RegulatorQueryRead;
  authority: FilingAuthority;
}) {
  const respond = useRespondToRegulatorQuery(bankId);
  const [body, setBody] = useState("");
  const [receipt, setReceipt] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [sent, setSent] = useState(false);

  if (query.status === "closed") {
    return (
      <p className="text-caption text-slate">
        {centralBankName()} closed this query. Nothing more is needed on it.
      </p>
    );
  }

  if (!authority.isResolved) return null;

  const correction =
    query.kind === "resubmission" ? (
      <CorrectionStep
        bankId={bankId}
        query={query}
        mayStart={authority.mayTransmit || authority.mayPrepare}
      />
    ) : null;

  if (!authority.mayTransmit) {
    return (
      <div className="flex flex-col gap-3 border-t border-border pt-4">
        {correction}
        <p className="text-caption text-slate">
          Replies to {centralBankName()} are sent by your Validator, the officer who transmits
          returns. Your account can read this query but not answer it.
        </p>
      </div>
    );
  }

  const ready = body.trim().length > 0 && !respond.isPending;
  const send = () =>
    respond.mutate(
      {
        number: query.number,
        body: body.trim(),
        resubmissionReceiptId: receipt.trim() || null,
      },
      {
        onSuccess: () => {
          setBody("");
          setReceipt("");
          setConfirming(false);
          setSent(true);
        },
        onError: () => setConfirming(false),
      },
    );

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-4">
      {correction}
      <label className="flex flex-col gap-1.5">
        <span className="text-caption font-medium text-navy">Your reply</span>
        <textarea
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            setSent(false);
          }}
          rows={5}
          maxLength={20000}
          placeholder={`Answer ${centralBankName()}'s question. The reply is sent as written and cannot be withdrawn.`}
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
        />
      </label>
      {query.kind === "resubmission" ? (
        <label className="flex flex-col gap-1.5">
          <span className="text-caption font-medium text-navy">
            Corrected filing receipt (optional)
          </span>
          <input
            value={receipt}
            onChange={(event) => setReceipt(event.target.value)}
            maxLength={40}
            placeholder="SRG-26-0000002"
            className="w-64 rounded-md border border-border bg-surface px-3 py-2 font-mono text-caption text-navy"
          />
        </label>
      ) : null}
      {respond.isError ? (
        <ErrorPanel error={respond.error} title="The reply was not sent" />
      ) : null}
      {sent ? (
        <p role="status" className="text-caption text-positive">
          Sent to {centralBankName()}. It is in the thread above and in your audit trail.
        </p>
      ) : null}
      {confirming ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-warning/30 bg-warning-light px-4 py-3">
          <p className="text-caption text-navy">
            This reply goes to {centralBankName()} and cannot be withdrawn. Send it?
          </p>
          <button
            type="button"
            onClick={send}
            disabled={respond.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            {respond.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <Send size={13} aria-hidden />
            )}
            Send now
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="px-3 py-2 text-caption font-medium text-slate"
          >
            Keep editing
          </button>
        </div>
      ) : (
        <div>
          <button
            type="button"
            disabled={!ready}
            onClick={() => setConfirming(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            <Send size={13} aria-hidden />
            Send reply to {regShort()}
          </button>
        </div>
      )}
    </div>
  );
}

function Detail({ bankId, query }: { bankId: string; query: RegulatorQueryRead }) {
  const acknowledge = useAcknowledgeRegulatorQuery(bankId);
  const { effectiveAuthority } = useUserProfile();
  const authority = filingAuthorityFor(effectiveAuthority, bankId, {
    resolved: effectiveAuthority !== undefined,
  });
  const asked = useRef<string | null>(null);
  // Opening a query is when it reaches the bank: record that at the regulator once.
  useEffect(() => {
    if (query.acknowledgedAt === null && asked.current !== query.number) {
      asked.current = query.number;
      acknowledge.mutate(query.number);
    }
  }, [query.number, query.acknowledgedAt, acknowledge]);

  const opened = asDate(query.openedAt);
  const due = asDate(query.dueAt);
  const received = asDate(query.acknowledgedAt);
  return (
    <SectionCard
      title={query.subject}
      subtitle={`${query.number} · ${query.kind === "resubmission" ? "Request for a corrected filing" : "Query"} on receipt ${query.receiptId}`}
    >
      <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-2 text-caption sm:grid-cols-4">
        <div>
          <dt className="text-slate">Raised</dt>
          <dd className="text-navy">{opened ? fmtTimestamp(opened) : "—"}</dd>
        </div>
        <div>
          <dt className="text-slate">Reply due</dt>
          <dd className="text-navy">{due ? fmtTimestamp(due) : "No date set"}</dd>
        </div>
        <div>
          <dt className="text-slate">Received by your bank</dt>
          <dd className="text-navy">{received ? fmtTimestamp(received) : "Recording now…"}</dd>
        </div>
        <div>
          <dt className="text-slate">Return</dt>
          <dd className="text-navy">
            {query.filing ? (
              <Link
                className="text-action underline"
                href={`/submissions/approvals`}
              >
                {query.filing.returnCode} · {fmtDateUTC(asDate(query.filing.reportingDate) ?? new Date())} · v
                {query.filing.version}
              </Link>
            ) : (
              "Not a filing of this workspace"
            )}
          </dd>
        </div>
      </dl>
      <Thread query={query} />
      <div className="mt-5">
        <Reply bankId={bankId} query={query} authority={authority} />
      </div>
    </SectionCard>
  );
}

export default function RegulatorQueriesPage() {
  // useSearchParams reads the selected query from the URL; it needs a boundary.
  return (
    <Suspense>
      <RegulatorQueries />
    </Suspense>
  );
}

function RegulatorQueries() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const queries = useRegulatorQueries(bankId);
  const router = useRouter();
  const params = useSearchParams();
  const [now] = useState(() => new Date());
  const items = useMemo(() => queries.data?.items ?? [], [queries.data]);
  const selectedNumber = params.get("q") ?? items[0]?.number ?? null;
  const selected = items.find((query) => query.number === selectedNumber) ?? null;
  const awaiting = items.filter((query) => query.status === "awaiting_institution").length;
  const readAt = asDate(queries.data?.readAt);

  return (
    <PageContainer>
      <div className="mb-6">
        <p className="text-micro uppercase tracking-wider text-slate">Regulatory reporting</p>
        <h1 className="text-heading font-semibold text-navy">Queries from {centralBankName()}</h1>
        <p className="mt-1 text-body text-slate">
          Questions {regShort()} supervisors raise on your filings, read live from the AequorOS
          Reporting Gateway
          {readAt ? ` · checked ${fmtTimestamp(readAt)}` : ""}.
        </p>
      </div>

      {queries.isPending ? (
        <SkeletonCard />
      ) : queries.isError ? (
        <ErrorPanel error={queries.error} onRetry={() => void queries.refetch()} title="Could not read the queries" />
      ) : !queries.data.connected || (queries.data.message && items.length === 0) ? (
        <SectionCard title="No connection to read queries from">
          <p className="text-body text-slate">{queries.data.message}</p>
        </SectionCard>
      ) : items.length === 0 ? (
        <SectionCard title="No queries">
          <p className="flex items-center gap-2 text-body text-slate">
            <MessageSquare size={16} aria-hidden />
            {centralBankName()} has raised no query on your filings. When a supervisor does, it
            appears here within a minute.
          </p>
        </SectionCard>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(280px,360px)_1fr]">
          <SectionCard
            title={awaiting > 0 ? `${awaiting} awaiting your reply` : "All answered"}
            noPadding
          >
            <ul>
              {items.map((query) => (
                <QueryRow
                  key={query.number}
                  query={query}
                  now={now}
                  selected={query.number === selected?.number}
                  onSelect={() => router.replace(`/submissions/queries?q=${encodeURIComponent(query.number)}`)}
                />
              ))}
            </ul>
          </SectionCard>
          {selected && bankId ? <Detail key={selected.number} bankId={bankId} query={selected} /> : null}
        </div>
      )}
    </PageContainer>
  );
}
