import Link from "next/link";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { Notice } from "@/components/notice";
import { Card, Chip, PageHeader, PageProblem, Unavailable, cx, field } from "@/components/ui";
import type { QueryDetail, QueryFilter, QueryListItem, SubmissionRow } from "@/lib/api/types";
import { daysBetween, formatDate, formatDateTime, formatDayMonth, formatRelativeDay, formatTime, initials, isoDate, paragraphs, parseInstant } from "@/lib/format";
import { QUERY_KIND, queryStatus } from "@/lib/labels";
import { noticeFrom } from "@/lib/notice";
import { capabilities, isReadOnly } from "@/lib/roles";
import { INSTITUTION_CODE, oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";
import { load, optional } from "@/lib/server/load";
import { queryReply, raiseQuery } from "../actions";

const TABS: { key: QueryFilter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "awaiting_institution", label: "Awaiting institution" },
  { key: "awaiting_supervisor", label: "Awaiting supervisor" },
  { key: "closed", label: "Closed" },
];

function dueText(item: QueryListItem, now: Date): string {
  switch (item.status) {
    case "closed":
      return "Closed";
    case "overdue": {
      const days = item.due_at ? Math.max(1, daysBetween(item.due_at, now)) : 0;
      return days ? `${days} ${days === 1 ? "day" : "days"} overdue` : "Overdue";
    }
    case "awaiting_supervisor": {
      if (item.kind === "query") return "Reply received";
      const diff = daysBetween(item.last_activity_at, now);
      return diff === 0 ? "Refiled today" : diff === 1 ? "Refiled yesterday" : `Refiled ${formatRelativeDay(item.last_activity_at, now)}`;
    }
    default:
      if (!item.due_at) return "No reply date";
      return daysBetween(now, item.due_at) === 0 ? "Due today" : `Due ${formatDayMonth(item.due_at)}`;
  }
}

function threadHref(number: string, tab: QueryFilter): string {
  return `/queries/${number}${tab === "open" ? "" : `?status=${tab}`}`;
}

function NewQuery({ receipts, open }: { receipts: SubmissionRow[] | null; open: boolean }) {
  const today = isoDate(new Date());
  const due = new Date();
  due.setUTCDate(due.getUTCDate() + 3);
  return (
    <details className="relative" open={open || undefined}>
      <summary className="inline-flex min-h-11 cursor-pointer list-none items-center rounded-lg bg-action px-[18px] text-[14px] font-semibold text-white hover:bg-action-hover [&::-webkit-details-marker]:hidden">
        New query
      </summary>
      <div className="absolute left-0 z-30 mt-2 w-[min(420px,calc(100vw-32px))] rounded-[10px] border border-line bg-white p-4 shadow-[0_12px_32px_rgba(14,23,38,0.16)] sm:left-auto sm:right-0">
        <ActionForm action={raiseQuery} label="New query" className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="n-receipt" className={field.label}>Filing</label>
            {receipts && receipts.length > 0 ? (
              <select id="n-receipt" name="receipt_id" required className={field.select}>
                {receipts.map((r) => (
                  <option key={r.receipt_id} value={r.receipt_id}>
                    {r.receipt_id} · {r.return_code} · {formatDate(r.reporting_date)} · v{r.version}
                  </option>
                ))}
              </select>
            ) : (
              <input id="n-receipt" name="receipt_id" required placeholder="Receipt, e.g. SRG-26-0114806" pattern="[Ss][Rr][Gg]-\d{2}-\d{7,}" className={cx(field.input, "font-mono")} />
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="n-kind" className={field.label}>Type</label>
            <select id="n-kind" name="kind" className={field.select} defaultValue="query">
              <option value="query">Query: ask for an explanation</option>
              <option value="resubmission">Resubmission: ask for a corrected filing</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="n-subject" className={field.label}>Subject</label>
            <input id="n-subject" name="subject" required maxLength={200} className={field.input} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="n-body" className={field.label}>Message</label>
            <textarea id="n-body" name="body" required rows={4} placeholder="Write to the institution. They receive it through the API and in the institution portal." className={field.textarea} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="n-due" className={field.label}>Reply due (17:00 GMT)</label>
            <input id="n-due" name="due" type="date" required min={today} defaultValue={isoDate(due)} className={field.input} />
          </div>
          <SubmitButton pendingLabel="Sending…">Send to institution</SubmitButton>
        </ActionForm>
      </div>
    </details>
  );
}

function Thread({ q, canReply }: { q: QueryDetail; canReply: boolean }) {
  const status = queryStatus(q.status);
  const dueDate = q.due_at ? parseInstant(q.due_at) : null;
  const defaultDue = dueDate && dueDate > new Date() ? isoDate(dueDate) : isoDate(new Date(Date.now() + 3 * 86_400_000));
  return (
    <Card aria-labelledby="thread-title" className="flex-[2_1_560px]">
      <div className="flex flex-col gap-2.5 border-b border-hairline px-[22px] py-5">
        <div className="flex flex-wrap items-start justify-between gap-2.5">
          <div className="flex flex-col gap-1.5">
            <h2 id="thread-title" className="m-0 text-[19px] font-semibold">{q.subject}</h2>
            <span className="text-[14px] text-ink-2">
              {q.institution_name} · {q.kind === "resubmission" ? "resubmission request on" : "query on"}{" "}
              <Link href={`/submissions/${q.receipt_id}`} className="font-mono text-[13px]">{q.receipt_id}</Link> · {q.return_code}, {formatDate(q.reporting_date)}
            </span>
          </div>
          <Chip tone={status.tone}>{status.label}</Chip>
        </div>
        <div className="flex flex-wrap gap-x-[18px] gap-y-1.5 text-[13px] text-muted">
          <span>Opened by {q.opened_by}, {formatDateTime(q.opened_at)}</span>
          {q.due_at && (
            <span>
              Reply due <strong className="font-semibold text-ink">{formatDateTime(q.due_at)}</strong>
            </span>
          )}
          <span>
            {q.delivered_at ? `Delivered to the institution’s system ${formatTime(q.delivered_at)}` : "Not yet delivered"}
            {q.delivered_at && (q.acknowledged_at ? " · acknowledged" : " · not yet acknowledged")}
          </span>
        </div>
      </div>
      <ol className="m-0 flex list-none flex-col gap-4 px-[22px] py-5">
        {q.messages.map((m) =>
          m.author === "system" ? (
            <li key={m.id} className="flex items-center gap-3 pl-12 text-[13px] text-muted">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="flex-none">
                <path d="M5 12l4 4L19 6" />
              </svg>
              <span>{m.body}</span>
            </li>
          ) : (
            <li key={m.id} className="flex gap-3">
              <span aria-hidden="true" className={cx("flex size-9 flex-none items-center justify-center rounded-full text-[13px] font-semibold", m.author === "supervisor" ? "bg-action text-white" : "bg-neutral-soft text-ink-2")}>
                {initials(m.author_label)}
              </span>
              <div className="flex min-w-0 flex-auto flex-col gap-1.5">
                <span className="text-[13px] text-muted">
                  <strong className="font-semibold text-ink">{m.author_label}</strong>
                  {m.author_role && ` · ${m.author_role}`} · <time dateTime={m.created_at}>{formatDateTime(m.created_at, { year: false })}</time>
                </span>
                <div className={cx("flex flex-col gap-2.5 rounded-[10px] px-4 py-3.5 text-[14px] leading-[1.6] text-ink", m.author === "supervisor" ? "bg-canvas" : "border border-line bg-white")}>
                  {paragraphs(m.body).map((p, i) => (
                    <p key={i} className="m-0 whitespace-pre-line">{p}</p>
                  ))}
                </div>
              </div>
            </li>
          ),
        )}
      </ol>
      {q.status === "closed" ? (
        <p className="m-0 mt-auto border-t border-hairline px-[22px] py-4 text-[14px] text-muted">This query is closed. Raise a new query to ask the institution anything further.</p>
      ) : canReply ? (
        <ActionForm action={queryReply.bind(null, q.number)} label="Reply" className="mt-auto flex flex-col gap-3 border-t border-hairline px-[22px] pb-[22px] pt-[18px]">
          <label htmlFor="reply" className="text-[13px] font-semibold text-ink-2">Add to this query</label>
          <textarea id="reply" name="body" rows={4} required placeholder="Write to the institution. They receive it through the API and in the institution portal." className={field.textarea} />
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-4">
              <label className="inline-flex min-h-11 items-center gap-2 text-[14px] text-ink-2">
                <input type="checkbox" name="require_resubmission" className="size-[18px]" defaultChecked={q.kind === "resubmission"} />
                Require a corrected filing (resubmission)
              </label>
              <label className="inline-flex items-center gap-2 text-[14px] text-ink-2">
                Reply due
                <input type="date" name="due" min={isoDate(new Date())} defaultValue={defaultDue} className="h-11 rounded-lg border border-field px-2.5 text-[14px]" />
              </label>
            </div>
            <div className="flex flex-wrap gap-2.5">
              <Unavailable reason="Attachments are not offered by the supervisor API yet">Attach file</Unavailable>
              <SubmitButton variant="secondary" intent="close" skipValidation pendingLabel="Closing…">Close query</SubmitButton>
              <SubmitButton intent="send" pendingLabel="Sending…">Send to institution</SubmitButton>
            </div>
          </div>
        </ActionForm>
      ) : (
        <p className="m-0 mt-auto border-t border-hairline px-[22px] py-4 text-[14px] text-muted">Your role can read this query but not reply to it.</p>
      )}
    </Card>
  );
}

export async function QueriesView({ params, selected }: { params: SearchParams; selected: string | null }) {
  const tab = oneOf(param(params, "status"), TABS.map((t) => t.key), "open");
  const institution = param(params, "institution_code");
  const api = await getApi();
  const [list, thread, me, receipts] = await Promise.all([
    load(api.listQueries({ status: tab })),
    selected ? load(api.getQuery(selected)) : Promise.resolve(null),
    getMe(),
    institution && INSTITUTION_CODE.test(institution) ? optional(api.listSubmissions({ institution_code: institution, limit: 20 })) : Promise.resolve(null),
  ]);
  const crumbs = [{ label: "Supervision" }, { label: "Queries" }];
  if (!list.ok) return <PageProblem crumbs={crumbs} title="Queries and resubmission requests" kind={list.kind} message={list.message} />;
  const supervise = capabilities.supervise(me.roles);
  const notice = noticeFrom(params, selected ? `/queries/${selected}` : "/queries");
  const now = new Date();

  return (
    <>
      {notice && <Notice {...notice} />}
      <PageHeader
        crumbs={crumbs}
        title="Queries and resubmission requests"
        subtitle="Questions to institutions about their filings, delivered through the API and the institution portal"
        actions={supervise ? <NewQuery receipts={receipts?.items.filter((r) => r.status !== "superseded") ?? null} open={param(params, "new") === "1"} /> : undefined}
      />
      <div className="flex flex-wrap items-start gap-5">
        <Card aria-label="Threads" className="flex-[1_1_340px]">
          <nav aria-label="Thread status" className="flex flex-wrap gap-1 border-b border-line px-2.5 pt-1.5">
            {TABS.map((t) => {
              const on = t.key === tab;
              return (
                <Link
                  key={t.key}
                  href={t.key === "open" ? "/queries" : `/queries?status=${t.key}`}
                  aria-current={on ? "page" : undefined}
                  className={cx("-mb-px inline-flex min-h-11 items-center border-b-2 px-3 text-[13px] no-underline", on ? "border-action font-semibold text-ink hover:text-ink" : "border-transparent font-medium text-muted hover:text-ink")}
                >
                  {t.label} · {list.data.counts[t.key]}
                </Link>
              );
            })}
          </nav>
          {list.data.items.length === 0 ? (
            <p className="m-0 px-[18px] py-6 text-[14px] text-muted">No queries here.</p>
          ) : (
            <ul className="m-0 list-none p-0">
              {list.data.items.map((item) => {
                const on = item.number === selected;
                const status = queryStatus(item.status);
                return (
                  <li key={item.number} className={cx("border-b border-hairline", on ? "bg-selected" : "bg-white")}>
                    <Link href={threadHref(item.number, tab)} aria-current={on ? "true" : undefined} className="flex flex-col gap-1.5 px-[18px] py-3.5 text-ink no-underline hover:bg-panel hover:text-ink">
                      <span className="flex items-center justify-between gap-2.5">
                        <span className="text-[14px] font-semibold">{item.institution_name}</span>
                        <span className="whitespace-nowrap text-[12px] text-muted">{formatRelativeDay(item.last_activity_at, now)}</span>
                      </span>
                      <span className="text-[14px] leading-[1.4] text-ink-2">{item.subject}</span>
                      <span className="flex flex-wrap items-center gap-2">
                        <Chip tone={status.tone} className="px-[9px] py-0.5">{status.label}</Chip>
                        <span className="text-[12px] text-muted">
                          {QUERY_KIND[item.kind]} · <span className="font-mono">{item.return_code}</span>
                        </span>
                        <span className="text-[12px] text-muted">{dueText(item, now)}</span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
          {list.data.next_cursor && <p className="m-0 px-[18px] py-3 text-[13px] text-muted">More threads exist; narrow the list with the tabs above.</p>}
        </Card>
        {thread === null ? (
          <Card className="flex-[2_1_560px] items-center justify-center px-[22px] py-16 text-center">
            <p className="m-0 text-[14px] text-muted">Select a query to read the thread{supervise ? " and reply" : ""}.</p>
          </Card>
        ) : thread.ok ? (
          <Thread q={thread.data} canReply={supervise && !isReadOnly(me.roles)} />
        ) : (
          <Card className="flex-[2_1_560px] px-[22px] py-6">
            <p role="alert" className="m-0 text-[14px] text-note-text">{thread.message}</p>
          </Card>
        )}
      </div>
    </>
  );
}
