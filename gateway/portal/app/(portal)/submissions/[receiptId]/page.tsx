import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { ActionDisclosure } from "@/components/disclosure";
import { StatusChip } from "@/components/filings";
import { Notice } from "@/components/notice";
import { Breadcrumb, Card, CardTitle, Chip, PageProblem, Progress, Row, button, cx, field } from "@/components/ui";
import type { ContentLine, SubmissionDetail } from "@/lib/api/types";
import {
  formatDate,
  formatDateTime,
  formatDayMonth,
  formatDigest,
  formatDurationMs,
  formatGatewayKey,
  formatMonthName,
  formatFigure,
  formatNumber,
  formatTime,
  isoDate,
  parseInstant,
  unitPhrase,
} from "@/lib/format";
import { BASIS, channelLabel, institutionTypeLabel } from "@/lib/labels";
import { noticeFrom } from "@/lib/notice";
import { capabilities, isReadOnly, submissionActions } from "@/lib/roles";
import { RECEIPT_ID, oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";
import { load } from "@/lib/server/load";
import { acceptFiling, assignToMe, raiseQuery, requestResubmission } from "../../actions";

type Props = { params: Promise<{ receiptId: string }>; searchParams: Promise<SearchParams> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { receiptId } = await params;
  return { title: receiptId };
}

function defaultDue(): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + 3);
  return isoDate(d);
}

function activityTime(at: string, receivedAt: string): string {
  const a = parseInstant(at);
  const r = parseInstant(receivedAt);
  if (!a || !r) return "—";
  if (isoDate(a) !== isoDate(r)) return `${formatDayMonth(a)}, ${formatTime(a)}`;
  return Math.abs(a.getTime() - r.getTime()) < 60_000 ? formatTime(a, { seconds: true }) : formatTime(a);
}

function ranAt(detail: SubmissionDetail): string {
  const ran = parseInstant(detail.validation.ran_at);
  const received = parseInstant(detail.row.received_at);
  const when = ran && received && Math.abs(ran.getTime() - received.getTime()) < 60_000 ? "run at receipt" : `run ${formatDateTime(detail.validation.ran_at, { year: false })}`;
  return `${when} in ${formatDurationMs(detail.validation.duration_ms)}`;
}

function Decisions({ detail }: { detail: SubmissionDetail }) {
  const can = submissionActions(detail.allowed_actions);
  const id = detail.row.receipt_id;
  const due = defaultDue();
  const today = isoDate(new Date());
  return (
    <div className="flex flex-wrap gap-2.5">
      {can.assign && (
        <ActionForm action={assignToMe.bind(null, id)} label="Assign to me" className="flex flex-col gap-1" feedbackClassName="max-w-56">
          <SubmitButton variant="secondary" pendingLabel="Assigning…">
            Assign to me
          </SubmitButton>
        </ActionForm>
      )}
      {can.query && (
        <ActionDisclosure label="Raise query" group="decision">
          <ActionForm action={raiseQuery} label="Raise a query" className="flex flex-col gap-3">
            <input type="hidden" name="receipt_id" value={id} />
            <input type="hidden" name="kind" value="query" />
            <p className="m-0 text-[13px] text-muted">The institution receives this through the API and in its portal inbox.</p>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="q-subject" className={field.label}>Subject</label>
              <input id="q-subject" name="subject" required maxLength={200} className={field.input} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="q-body" className={field.label}>Question</label>
              <textarea id="q-body" name="body" required rows={4} className={field.textarea} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="q-due" className={field.label}>Reply due (17:00 GMT)</label>
              <input id="q-due" name="due" type="date" required min={today} defaultValue={due} className={field.input} />
            </div>
            <SubmitButton pendingLabel="Sending…">Send query</SubmitButton>
          </ActionForm>
        </ActionDisclosure>
      )}
      {can.resubmission && (
        <ActionDisclosure label="Request resubmission" group="decision">
          <ActionForm action={requestResubmission.bind(null, id)} label="Request resubmission" className="flex flex-col gap-3">
            <p className="m-0 text-[13px] text-muted">The institution must refile a corrected version. This filing stays on record.</p>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="r-reason" className={field.label}>What needs correcting</label>
              <textarea id="r-reason" name="reason" required rows={4} className={field.textarea} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="r-due" className={field.label}>Corrected filing due (17:00 GMT)</label>
              <input id="r-due" name="due" type="date" required min={today} defaultValue={due} className={field.input} />
            </div>
            <SubmitButton pendingLabel="Sending…">Send request</SubmitButton>
          </ActionForm>
        </ActionDisclosure>
      )}
      {can.accept && (
        <ActionDisclosure label="Accept filing" variant="primary" group="decision">
          <ActionForm action={acceptFiling.bind(null, id)} label="Accept filing" className="flex flex-col gap-3">
            <p className="m-0 text-[13px] text-muted">Accepting is recorded in the audit log and told to the institution. It cannot be undone.</p>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="a-note" className={field.label}>Note (optional, internal)</label>
              <textarea id="a-note" name="note" rows={3} maxLength={2000} className={field.textarea} />
            </div>
            <SubmitButton pendingLabel="Accepting…">Confirm acceptance</SubmitButton>
          </ActionForm>
        </ActionDisclosure>
      )}
    </div>
  );
}

function lineAnchor(code: string): string {
  return `line-${encodeURIComponent(code)}`;
}

/** Line codes a validation issue points at (`/lines/<code>`, JSON-pointer escaped). */
function flaggedLines(detail: SubmissionDetail): Set<string> {
  const out = new Set<string>();
  for (const issue of detail.validation?.issues ?? []) {
    const code = /^\/lines\/(.+)$/.exec(issue.pointer ?? "")?.[1];
    if (code) out.add(code.replace(/~1/g, "/").replace(/~0/g, "~"));
  }
  return out;
}

const DOCUMENT_KINDS: Record<string, string> = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel workbook",
  "application/pdf": "PDF",
  "text/csv": "CSV",
  "application/zip": "CSV, one file per sheet (zip)",
};

function documentKind(mediaType: string): string {
  return DOCUMENT_KINDS[mediaType] ?? "Document";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Up to this many lines, a sectioned return opens every section (BSD5A runs to 255). */
const READABLE_LINES = 400;

function isBlank(line: ContentLine): boolean {
  return line.value === null && line.previous_version_value === null && line.prior_period_value === null;
}

function LineRow({ line, withPrevious, flagged }: { line: ContentLine; withPrevious: boolean; flagged: boolean }) {
  const changed = withPrevious && line.changed;
  const cell = "border-b border-hairline px-3 py-2.5 text-right";
  return (
    <tr id={lineAnchor(line.code)} className={flagged ? "bg-bad-soft" : changed ? "bg-changed" : line.is_total ? "bg-panel" : "bg-white"}>
      <td className="border-b border-hairline py-2.5 pr-3 font-mono text-[13px] text-ink-2">{line.code}</td>
      <td className={cx("border-b border-hairline px-3 py-2.5", line.is_total && "font-semibold")}>
        {line.label}
        {changed && <span className="ml-2 rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn">Changed</span>}
        {flagged && <span className="ml-2 rounded-full bg-white px-2 py-0.5 text-[11px] font-semibold text-bad">Flagged</span>}
      </td>
      <td className={cx(cell, (changed || line.is_total) && "font-semibold")}>{formatFigure(line.value)}</td>
      {withPrevious && <td className={cx(cell, "text-muted")}>{formatFigure(line.previous_version_value)}</td>}
      <td className="border-b border-hairline py-2.5 pl-3 text-right text-muted">{formatFigure(line.prior_period_value)}</td>
    </tr>
  );
}

function LineHead({ detail, withPrevious, previousVersion }: { detail: SubmissionDetail; withPrevious: boolean; previousVersion: number | null }) {
  return (
    <thead>
      <tr className="text-[12px] text-muted">
        <th scope="col" className="border-b border-line py-2 pr-3 text-left font-semibold">Line</th>
        <th scope="col" className="border-b border-line px-3 py-2 text-left font-semibold">Item</th>
        <th scope="col" className="border-b border-line px-3 py-2 text-right font-semibold">
          {formatDate(detail.row.reporting_date)} · v{detail.row.version}
        </th>
        {withPrevious && <th scope="col" className="border-b border-line px-3 py-2 text-right font-semibold">v{previousVersion}</th>}
        <th scope="col" className="border-b border-line py-2 pl-3 text-right font-semibold">Prior period</th>
      </tr>
    </thead>
  );
}

/**
 * The filed figures. A return whose definition has sections (the official forms run to
 * thousands of lines over many sheets) shows one collapsible block per section, with
 * blank cells hidden unless asked for. A return short enough to read on one page opens
 * every section; a longer one opens its first section, and any section holding a flagged
 * line, or a change in the changes view, opens on its own.
 */
function ContentTable({
  detail,
  view,
  previousVersion,
  showBlank,
}: {
  detail: SubmissionDetail;
  view: "summary" | "changes" | "full";
  previousVersion: number | null;
  showBlank: boolean;
}) {
  const withPrevious = view === "changes" && previousVersion !== null;
  const { currency, unit, sections } = detail.content;
  const flagged = flaggedLines(detail);
  const visible = detail.content.lines.filter(
    (line) =>
      (view !== "summary" || line.is_total || flagged.has(line.code)) &&
      (view !== "changes" || sections.length === 0 || line.changed || flagged.has(line.code)) &&
      (showBlank || !isBlank(line) || flagged.has(line.code)),
  );
  if (sections.length === 0) {
    return (
      <table className="tabular w-full min-w-[720px] border-collapse text-[14px]">
        <caption className="pb-2 text-left text-[12px] text-muted">
          {unit ? `Amounts in ${currency} ${unit}` : `Amounts in ${currency}`}
        </caption>
        <LineHead detail={detail} withPrevious={withPrevious} previousVersion={previousVersion} />
        <tbody>
          {visible.map((line) => (
            <LineRow key={line.code} line={line} withPrevious={withPrevious} flagged={flagged.has(line.code)} />
          ))}
        </tbody>
      </table>
    );
  }
  const all = detail.content.lines;
  const readable = all.length <= READABLE_LINES;
  return (
    <div className="flex flex-col gap-2">
      <p className="m-0 text-[12px] text-muted">
        Amounts in {currency}; each section states its unit. {all.filter((l) => !isBlank(l)).length.toLocaleString("en-US")} of{" "}
        {all.length.toLocaleString("en-US")} lines carry a figure
        {showBlank ? "." : "; blank cells are hidden."}
      </p>
      {sections.map((section, index) => {
        const inSection = all.filter((line) => line.section === section.code);
        const rows = visible.filter((line) => line.section === section.code);
        if (inSection.length === 0) return null;
        const filled = inSection.filter((line) => !isBlank(line)).length;
        const changedCount = inSection.filter((line) => line.changed).length;
        const hasFlag = inSection.some((line) => flagged.has(line.code));
        const open = readable || index === 0 || hasFlag || (view === "changes" && changedCount > 0);
        return (
          <details key={section.code} open={open} className="rounded-lg border border-hairline">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3.5 py-2.5 text-[14px]">
              <span className="font-semibold text-ink">{section.title}</span>
              {section.unit && <span className="text-[12px] text-muted">{unitPhrase(section.unit, currency)}</span>}
              <span className="text-[12px] text-muted">
                {filled.toLocaleString("en-US")} of {inSection.length.toLocaleString("en-US")} lines filled
              </span>
              {changedCount > 0 && withPrevious && (
                <span className="rounded-full bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn">{changedCount} changed</span>
              )}
              {hasFlag && <span className="rounded-full bg-bad-soft px-2 py-0.5 text-[11px] font-semibold text-bad">Flagged</span>}
            </summary>
            {rows.length === 0 ? (
              <p className="m-0 px-3.5 pb-3 text-[13px] text-muted">Nothing to show in this view.</p>
            ) : (
              <div className="overflow-x-auto px-3.5 pb-3">
                <table className="tabular w-full min-w-[720px] border-collapse text-[14px]">
                  <LineHead detail={detail} withPrevious={withPrevious} previousVersion={previousVersion} />
                  <tbody>
                    {rows.map((line) => (
                      <LineRow key={line.code} line={line} withPrevious={withPrevious} flagged={flagged.has(line.code)} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </details>
        );
      })}
    </div>
  );
}

export default async function SubmissionPage({ params, searchParams }: Props) {
  const { receiptId: raw } = await params;
  const receiptId = decodeURIComponent(raw);
  if (!RECEIPT_ID.test(receiptId)) notFound();
  const query = await searchParams;
  const api = await getApi();
  const [result, me] = await Promise.all([load(api.getSubmission(receiptId)), getMe()]);
  const crumbs = [{ label: "Submissions", href: "/submissions" }, { label: receiptId, mono: true }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title={receiptId} kind={result.kind} message={result.message} />;
  const d = result.data;
  const row = d.row;
  const previous = d.versions.find((v) => v.receipt_id === d.amendment?.supersedes_receipt_id) ?? null;
  const previousVersion = d.amendment ? (previous?.version ?? row.version - 1) : null;
  const view = oneOf(param(query, "view"), ["summary", "changes", "full"] as const, d.amendment ? "changes" : "full");
  const effectiveView = view === "changes" && !d.amendment ? "full" : view;
  const notice = noticeFrom(query, `/submissions/${receiptId}`);
  const can = submissionActions(d.allowed_actions);
  const ev = d.evidence;
  const verified = ev.signature.valid && ev.certificate.valid && ev.receipt_signature.valid && ev.chain_intact;
  const showBlank = param(query, "blank") === "1";
  const sectioned = d.content.sections.length > 0;
  const viewHref = (v: string, blank = showBlank) =>
    `/submissions/${receiptId}?view=${v}${blank ? "&blank=1" : ""}#return-content`;

  const evidence: { label: string; value: string; mono?: boolean }[] = [
    { label: "Receipt", value: ev.receipt_id, mono: true },
    { label: "Payload digest (SHA-256)", value: formatDigest(ev.payload_sha256), mono: true },
    { label: "Institution signature", value: `${ev.signature.valid ? "Valid" : "Not valid"} · ${ev.signature.alg} · key ${ev.signature.kid}` },
    { label: "Signing key custody", value: `${ev.signature.custody_note} · registered with the Bank ${formatDate(ev.signature.key_registered_at)}` },
    { label: "Client certificate", value: `${ev.certificate.valid ? "Valid" : "Not valid"} · mTLS · expires ${formatDate(ev.certificate.not_after)}` },
    { label: "Gateway receipt signature", value: `${ev.receipt_signature.valid ? "Valid" : "Not valid"} · gateway key ${formatGatewayKey(ev.receipt_signature.kid)}` },
    { label: "Trusted timestamp", value: ev.received_at, mono: true },
    { label: "Evidence ledger", value: `Entry ${formatNumber(ev.ledger_seq)} · ${ev.chain_intact ? "chain intact" : "chain broken"}` },
  ];

  return (
    <>
      {notice && <Notice {...notice} />}
      <header className="flex flex-col gap-3.5">
        <Breadcrumb items={crumbs} />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="m-0 text-[26px] font-semibold tracking-[-0.01em]">
                {row.institution_name} — {row.return_code} {row.return_name}
              </h1>
              <StatusChip state={row.status} />
            </div>
            <div className="flex flex-wrap gap-x-[18px] gap-y-1.5 text-[14px] text-ink-2">
              <span>Reporting date <strong className="font-semibold text-ink">{formatDate(row.reporting_date)}</strong></span>
              <span>
                Version <strong className="font-semibold text-ink">{row.version}</strong>
                {previousVersion !== null && ` (supersedes v${previousVersion})`}
              </span>
              <span>Received <strong className="font-semibold text-ink">{formatDateTime(row.received_at, { seconds: true, zone: true })}</strong></span>
              <span>Channel <strong className="font-semibold text-ink">{channelLabel(row.channel)}</strong></span>
              <span>
                Software <strong className="font-semibold text-ink">{row.software_declared ?? "None declared"}</strong>
                {row.software_declared && " (declared)"}
              </span>
              <span>Reviewer <strong className="font-semibold text-ink">{row.reviewer_name ?? "Not assigned"}</strong></span>
            </div>
          </div>
          {can.any ? (
            <Decisions detail={d} />
          ) : (
            <p className="m-0 max-w-sm text-[13px] leading-[1.5] text-muted">
              {isReadOnly(me.roles) || !capabilities.supervise(me.roles)
                ? "Your role can read this filing but not decide on it."
                : row.status === "accepted" || row.status === "superseded"
                  ? "No decision is open on this filing."
                  : "Decisions on this filing are taken by its assigned supervisor."}
            </p>
          )}
        </div>
      </header>

      {d.amendment && (
        <div role="note" className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-note-line bg-note px-[18px] py-3.5">
          <div className="flex flex-col gap-1 text-[14px] leading-[1.5] text-note-text">
            <span className="font-semibold">
              Amendment: version {row.version} replaces version {previousVersion} ({d.amendment.supersedes_receipt_id}, filed {formatDateTime(d.amendment.supersedes_received_at)})
            </span>
            <span>
              Institution’s reason: “{d.amendment.reason}” Version {previousVersion} stays on record, marked superseded.
            </span>
          </div>
          <Link href={viewHref("changes")} className="inline-flex min-h-11 items-center text-[14px] font-semibold text-note-link">
            Show what changed
          </Link>
        </div>
      )}

      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[2_1_600px] flex-col gap-5">
          <Card aria-labelledby="validation-title" className="gap-4 px-[22px] py-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <CardTitle id="validation-title">Validation</CardTitle>
              <span className="text-[13px] text-muted">
                Rule set <span className="font-mono">{d.validation.rule_set}</span> · {ranAt(d)}
              </span>
            </div>
            {d.validation.groups.length > 0 ? (
              <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
                {d.validation.groups.map((g) => (
                  <div key={g.label} className="flex flex-col gap-1 rounded-lg border border-hairline px-3.5 py-3">
                    <span className="text-[13px] text-muted">{g.label}</span>
                    <span className={cx("tabular text-[18px] font-semibold", g.passed === g.total ? "text-ok" : "text-bad")}>
                      {g.passed} / {g.total}
                      {g.passed !== g.total && <span className="sr-only"> passed, {g.total - g.passed} failed</span>}
                    </span>
                    <span className="text-[12px] text-muted">{g.note}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="m-0 text-[14px] text-muted">Validation is still running. Results appear here when it finishes.</p>
            )}
            {d.validation.issues.map((issue, i) => (
              <div key={`${issue.rule_id}-${i}`} className="flex items-start gap-3 rounded-lg bg-panel px-3.5 py-3">
                <Chip tone={issue.severity === "error" ? "red" : "amber"} className="flex-none">
                  {issue.severity === "error" ? "Error" : "Warning"}
                </Chip>
                <span className="text-[14px] leading-[1.5] text-ink-2">
                  {issue.message}
                  {issue.pointer?.startsWith("/lines/") && (
                    <>
                      {" "}
                      <a href={`#${lineAnchor(issue.pointer.slice(7).replace(/~1/g, "/").replace(/~0/g, "~"))}`} className="text-action">
                        Go to line
                      </a>
                    </>
                  )}
                </span>
              </div>
            ))}
            {d.validation.groups.length > 0 && d.validation.issues.length === 0 && <p className="m-0 text-[14px] text-muted">Every rule passed with no warnings.</p>}
          </Card>

          <Card id="return-content" aria-labelledby="content-title" className="gap-3.5 px-[22px] py-5">
            <div className="flex flex-wrap items-center justify-between gap-2.5">
              <CardTitle id="content-title">Return content</CardTitle>
              <nav aria-label="View" className="inline-flex overflow-hidden rounded-lg border border-field">
                {(
                  [
                    ["summary", "Summary"],
                    ...(d.amendment ? [["changes", `Changes from v${previousVersion}`]] : []),
                    ["full", "Full return"],
                  ] as [string, string][]
                ).map(([key, label], i) => (
                  <Link
                    key={key}
                    href={viewHref(key)}
                    aria-current={effectiveView === key ? "true" : undefined}
                    className={cx(
                      "inline-flex min-h-11 items-center px-3.5 text-[13px] no-underline",
                      i > 0 && "border-l border-field",
                      effectiveView === key ? "bg-action-soft font-semibold text-action" : "bg-white text-ink-2 hover:bg-panel hover:text-ink",
                    )}
                  >
                    {label}
                  </Link>
                ))}
              </nav>
              {sectioned && (
                <Link href={viewHref(effectiveView, !showBlank)} className="text-[13px] text-action">
                  {showBlank ? "Hide blank cells" : "Show blank cells"}
                </Link>
              )}
            </div>
            <div className="overflow-x-auto">
              <ContentTable detail={d} view={effectiveView} previousVersion={previousVersion} showBlank={showBlank} />
            </div>
          </Card>

          <Card aria-labelledby="activity-title" className="gap-3.5 px-[22px] py-5">
            <CardTitle id="activity-title">Activity</CardTitle>
            <ol className="m-0 flex list-none flex-col p-0">
              {d.activity.map((event, i) => (
                <li key={`${event.at}-${i}`} className="flex gap-3.5 border-t border-hairline py-2.5">
                  <time dateTime={event.at} className="tabular flex-[0_0_76px] text-[13px] text-muted">
                    {activityTime(event.at, row.received_at)}
                  </time>
                  <span className="flex flex-col gap-0.5">
                    <span className="text-[14px] font-medium">{event.title}</span>
                    <span className="text-[13px] text-muted">{event.detail}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <aside className="flex min-w-0 flex-[1_1_320px] flex-col gap-5">
          <Card aria-labelledby="documents-title" className="gap-3.5 px-[22px] py-5">
            <CardTitle id="documents-title">Filed documents</CardTitle>
            {d.documents.length === 0 ? (
              <p className="m-0 text-[14px] leading-[1.5] text-muted">No documents were filed with this version; the return content above is the whole filing.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
                {d.documents.map((document) => (
                  <li key={document.name} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-hairline px-3.5 py-3">
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-[14px] font-semibold text-ink">{documentKind(document.media_type)}</span>
                      <span className="break-all font-mono text-[12px] text-ink-2">{document.name}</span>
                      <span className="text-[12px] text-muted">
                        {formatSize(document.bytes)} · SHA-256 {document.sha256.slice(0, 12)}…
                      </span>
                    </span>
                    <a href={`/export/document/${encodeURIComponent(receiptId)}/${encodeURIComponent(document.name)}`} className={button.small} download>
                      Download
                    </a>
                  </li>
                ))}
              </ul>
            )}
            {d.documents.length > 0 && (
              <span className="text-[12px] leading-[1.5] text-muted">Each document&apos;s digest is in the manifest the institution signed, so these are the files its officers approved.</span>
            )}
          </Card>

          <Card aria-labelledby="receipt-title" className="gap-3.5 px-[22px] py-5">
            <div className="flex items-baseline justify-between gap-2">
              <CardTitle id="receipt-title">Receipt and evidence</CardTitle>
              <Chip tone={verified ? "green" : "red"}>{verified ? "Verified" : "Not verified"}</Chip>
            </div>
            <dl className="m-0 flex flex-col gap-3">
              {evidence.map((fact) => (
                <div key={fact.label} className="flex flex-col gap-[3px]">
                  <dt className="text-[12px] text-muted">{fact.label}</dt>
                  <dd className={cx("m-0 leading-[1.5] text-ink", fact.mono ? "break-all font-mono text-[13px]" : "text-[14px] font-medium leading-[1.4]")}>{fact.value}</dd>
                </div>
              ))}
            </dl>
            <div className="flex flex-wrap gap-2">
              <a href={`/export/receipt/${encodeURIComponent(receiptId)}`} className={button.small} download>
                Download receipt
              </a>
              <a href={`/export/package/${encodeURIComponent(receiptId)}`} className={button.small} download>
                Download filed package
              </a>
            </div>
            <span className="text-[12px] leading-[1.5] text-muted">Anyone holding the receipt can verify both signatures against the published keys, without access to this system.</span>
          </Card>

          <Card aria-labelledby="prepared-title" className="gap-3 px-[22px] py-5">
            <CardTitle id="prepared-title">Declared by the institution</CardTitle>
            <dl className="m-0 flex flex-col gap-2.5 text-[14px]">
              <Row label="Prepared by">{d.declaration.prepared_by_role}</Row>
              <Row label="Approved by">{d.declaration.approved_by_role}</Row>
              <Row label="Approved at">{formatDateTime(d.declaration.approved_at)}</Row>
              <Row label="Basis">{BASIS[d.basis] ?? d.basis}</Row>
              <Row label="Software">{d.declaration.software ?? "None declared"}</Row>
            </dl>
          </Card>

          <Card aria-labelledby="inst-title" className="gap-3 px-[22px] py-5">
            <div className="flex items-baseline justify-between gap-2">
              <CardTitle id="inst-title">{d.institution.name}</CardTitle>
              <Link href={`/institutions/${d.institution.code}`} className="text-[13px] font-medium">
                Profile
              </Link>
            </div>
            <span className="text-[13px] text-muted">
              {institutionTypeLabel(d.institution.type)} · institution code {d.institution.code} · supervisor {d.institution.supervisor_name ?? "not assigned"}
            </span>
            <div className="flex flex-col gap-1.5">
              <div className="flex justify-between text-[13px]">
                <span className="text-ink-2">{formatMonthName(row.reporting_date)} cycle received</span>
                <span className="tabular font-semibold">
                  {d.institution.cycle_received} of {d.institution.cycle_due}
                </span>
              </div>
              <Progress value={d.institution.cycle_received} total={d.institution.cycle_due} label={`${d.institution.cycle_received} of ${d.institution.cycle_due} returns received`} />
              <span className="text-[12px] text-muted">{d.institution.outstanding.length ? `Outstanding: ${d.institution.outstanding.join("; ")}` : "Nothing outstanding for this cycle"}</span>
            </div>
          </Card>

          <Card aria-labelledby="versions-title" className="gap-2.5 px-[22px] py-5">
            <CardTitle id="versions-title">Versions</CardTitle>
            <ol className="m-0 flex list-none flex-col gap-2 p-0">
              {d.versions.map((v) => {
                const self = v.receipt_id === row.receipt_id;
                return (
                  <li key={v.receipt_id} className={cx("flex items-center justify-between gap-2.5 rounded-lg px-3 py-2.5", self && "bg-selected")}>
                    <span className="flex flex-col gap-0.5">
                      {self ? (
                        <span className="text-[14px] font-semibold">v{v.version} · this filing</span>
                      ) : (
                        <Link href={`/submissions/${v.receipt_id}`} className="text-[14px] font-semibold">
                          v{v.version} · {formatDateTime(v.received_at, { year: false })}
                        </Link>
                      )}
                      <span className="font-mono text-[12px] text-muted">{v.receipt_id}</span>
                    </span>
                    <StatusChip state={v.status} />
                  </li>
                );
              })}
            </ol>
          </Card>
        </aside>
      </div>
    </>
  );
}
