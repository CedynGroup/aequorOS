import type { Metadata } from "next";
import Link from "next/link";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { Card, Chip, PageHeader, PageProblem, button, cx, field, td, th } from "@/components/ui";
import type { AuditEntry, AuditListParams } from "@/lib/api/types";
import { detailValue, formatDate, formatDateTime, formatDigest, formatGatewayKey, formatNumber, formatTime, humanize, isoDate, shortHash } from "@/lib/format";
import { AUDIT_ACTORS, AUDIT_CATEGORY, auditCategory } from "@/lib/labels";
import { DATE, INSTITUTION_CODE, hrefWith, objectHref, oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load, optional } from "@/lib/server/load";
import { verifyChain } from "../actions";

export const metadata: Metadata = { title: "Audit log" };

const DATE_OPTIONS = [
  { value: "", label: "Any time" },
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "month", label: "This month" },
  { value: "custom", label: "Custom range" },
] as const;

function dateRange(choice: string, from: string | undefined, to: string | undefined): { from?: string; to?: string } {
  const today = new Date();
  const iso = isoDate(today);
  if (choice === "today") return { from: iso, to: iso };
  if (choice === "7d") return { from: isoDate(new Date(today.getTime() - 6 * 86_400_000)), to: iso };
  if (choice === "month") return { from: `${iso.slice(0, 8)}01`, to: iso };
  if (choice === "custom") return { from: from && DATE.test(from) ? from : undefined, to: to && DATE.test(to) ? to : undefined };
  return {};
}

function openLabel(ref: string): string {
  if (ref.startsWith("SRG-")) return "Open the filing";
  if (ref.startsWith("QRY-")) return "Open the query";
  if (ref.startsWith("SUP-")) return "Open the approval record";
  return "Open the institution";
}

function EntryDetail({ entry }: { entry: AuditEntry }) {
  const facts: { label: string; value: string; mono?: boolean }[] = [
    ...Object.entries(entry.detail).map(([key, value]) => ({ label: humanize(key), value: detailValue(value) })),
    { label: "Previous entry hash", value: formatDigest(entry.prev_hash), mono: true },
    { label: "This entry hash", value: formatDigest(entry.entry_hash), mono: true },
    { label: "Signed by", value: `Audit key ${formatGatewayKey(entry.signature_kid)} · ${entry.signature_valid ? "valid" : "signature not valid"}` },
  ];
  const href = objectHref(entry.object_ref);
  return (
    <>
      <div className="flex flex-col gap-1">
        <span className="text-[13px] text-muted">Selected entry</span>
        <h2 id="entry-title" className="m-0 text-[18px] font-semibold">{entry.action}</h2>
        <span className="text-[13px] text-muted">
          Entry {formatNumber(entry.seq)} · {formatDateTime(entry.occurred_at, { seconds: true, zone: true })}
        </span>
      </div>
      <dl className="m-0 flex flex-col gap-3">
        {facts.map((f) => (
          <div key={f.label} className="flex flex-col gap-[3px]">
            <dt className="text-[12px] text-muted">{f.label}</dt>
            <dd className={cx("m-0 text-ink", f.mono ? "break-all font-mono text-[13px] leading-[1.5]" : "text-[14px] font-medium leading-[1.45]", !entry.signature_valid && f.label === "Signed by" && "text-bad")}>{f.value}</dd>
          </div>
        ))}
      </dl>
      {href && (
        <Link href={href} className="text-[13px] font-medium">{openLabel(entry.object_ref)}</Link>
      )}
    </>
  );
}

export default async function AuditPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const actor = oneOf(param(params, "actor"), AUDIT_ACTORS.map((a) => a.value), "");
  const category = oneOf(param(params, "category"), ["", ...Object.keys(AUDIT_CATEGORY)], "");
  const institutionParam = param(params, "institution");
  const institution = institutionParam && INSTITUTION_CODE.test(institutionParam) ? institutionParam : "";
  const dateChoice = oneOf(param(params, "date"), DATE_OPTIONS.map((o) => o.value), "");
  const range = dateRange(dateChoice, param(params, "from"), param(params, "to"));
  const cursor = param(params, "cursor");
  const entryParam = Number(param(params, "entry"));
  const entrySeq = Number.isSafeInteger(entryParam) && entryParam > 0 ? entryParam : null;

  const query: AuditListParams = { actor_type: actor || undefined, category: category || undefined, institution_code: institution || undefined, ...range, cursor };
  const api = await getApi();
  const [result, institutions, entry] = await Promise.all([load(api.listAudit(query)), optional(api.listInstitutions()), entrySeq ? optional(api.getAuditEntry(entrySeq)) : Promise.resolve(null)]);
  const crumbs = [{ label: "Assurance" }, { label: "Audit log" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Audit log" kind={result.kind} message={result.message} />;
  const { integrity, siem, retention_label, items, next_cursor } = result.data;

  const current: Record<string, string | undefined> = {
    actor: actor || undefined,
    category: category || undefined,
    institution: institution || undefined,
    date: dateChoice || undefined,
    from: dateChoice === "custom" ? range.from : undefined,
    to: dateChoice === "custom" ? range.to : undefined,
    cursor,
  };
  const exportQuery = new URLSearchParams(Object.entries(range).filter((e): e is [string, string] => Boolean(e[1]))).toString();
  const verifiedToday = integrity.verified_at.slice(0, 10) === isoDate(new Date());
  const placeholder = retention_label.startsWith("[");

  return (
    <>
      <PageHeader
        crumbs={crumbs}
        title="Audit log"
        subtitle="Every sign-in, view, decision and configuration change, in a hash chain anyone can verify"
        actions={
          <>
            <ActionForm action={verifyChain} label="Verify the chain" className="flex flex-col items-end gap-1" feedbackClassName="max-w-80 text-right">
              <SubmitButton variant="secondary" pendingLabel="Verifying…">Verify chain now</SubmitButton>
            </ActionForm>
            <a href={`/export/audit${exportQuery ? `?${exportQuery}` : ""}`} className={button.primary} download>
              Export for auditors
            </a>
          </>
        }
      />

      <section aria-label="Integrity" className="flex flex-wrap gap-3.5">
        <div className={cx("flex flex-[2_1_420px] items-center gap-3.5 rounded-[10px] border px-[18px] py-4", integrity.intact ? "border-intact-line bg-intact" : "border-bad bg-bad-soft")}>
          <span aria-hidden="true" className={cx("flex size-10 flex-none items-center justify-center rounded-full", integrity.intact ? "bg-ok" : "bg-bad")}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d={integrity.intact ? "M5 12l4 4L19 6" : "M12 6v7 M12 17h.01"} />
            </svg>
          </span>
          <div className="flex min-w-0 flex-col gap-1">
            <span className={cx("text-[15px] font-semibold", integrity.intact ? "text-intact-title" : "text-bad")}>
              {integrity.intact ? "Chain intact" : "Chain broken"} · {formatNumber(integrity.entries)} entries
            </span>
            <span className={cx("text-[13px]", integrity.intact ? "text-intact-text" : "text-bad")}>
              Verified at {verifiedToday ? formatTime(integrity.verified_at) : formatDateTime(integrity.verified_at)} · head <span className="font-mono">{shortHash(integrity.head_hash)}</span> · checked every 30 minutes
            </span>
          </div>
        </div>
        <div className="flex flex-[1_1_260px] flex-col gap-1 rounded-[10px] border border-line bg-white px-[18px] py-4">
          <span className="text-[13px] text-muted">Copy to the Bank’s SIEM</span>
          <span className={cx("text-[15px] font-semibold", !siem.streaming && "text-bad")}>
            {siem.streaming ? `Streaming · ${siem.lag_seconds === null ? "lag not measured" : `${formatNumber(siem.lag_seconds)} s behind`}` : "Not streaming"}
          </span>
          <span className="text-[12px] text-muted">The Bank keeps its own copy, outside our control</span>
        </div>
        <div className="flex flex-[1_1_220px] flex-col gap-1 rounded-[10px] border border-line bg-white px-[18px] py-4">
          <span className="text-[13px] text-muted">Retention</span>
          <span className={cx("text-[15px] font-semibold", placeholder && "text-placeholder")}>{retention_label}</span>
          <span className="text-[12px] text-muted">Entries cannot be edited or deleted</span>
        </div>
      </section>

      <form aria-label="Filters" method="get" action="/audit" className="flex flex-wrap items-end gap-3 rounded-[10px] border border-line bg-white px-[18px] py-4">
        <div className="flex flex-[1_1_180px] flex-col gap-1.5">
          <label htmlFor="a-actor" className={field.label}>Actor</label>
          <select id="a-actor" name="actor" defaultValue={actor} className={field.select}>
            {AUDIT_ACTORS.map((a) => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_180px] flex-col gap-1.5">
          <label htmlFor="a-category" className={field.label}>Category</label>
          <select id="a-category" name="category" defaultValue={category} className={field.select}>
            <option value="">All categories</option>
            {Object.entries(AUDIT_CATEGORY).map(([value, c]) => (
              <option key={value} value={value}>{c.option}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_180px] flex-col gap-1.5">
          <label htmlFor="a-institution" className={field.label}>Institution</label>
          <select id="a-institution" name="institution" defaultValue={institution} className={field.select}>
            <option value="">All institutions</option>
            {(institutions?.items ?? []).map((i) => (
              <option key={i.code} value={i.code}>{i.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_180px] flex-col gap-1.5">
          <label htmlFor="a-date" className={field.label}>Date</label>
          <select id="a-date" name="date" defaultValue={dateChoice} className={field.select}>
            {DATE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        {dateChoice === "custom" && (
          <>
            <div className="flex flex-[1_1_150px] flex-col gap-1.5">
              <label htmlFor="a-from" className={field.label}>From</label>
              <input id="a-from" name="from" type="date" defaultValue={range.from} className={field.input} />
            </div>
            <div className="flex flex-[1_1_150px] flex-col gap-1.5">
              <label htmlFor="a-to" className={field.label}>To</label>
              <input id="a-to" name="to" type="date" defaultValue={range.to} className={field.input} />
            </div>
          </>
        )}
        <div className="flex gap-2">
          <button type="submit" className={button.secondary}>Apply filters</button>
          <Link href="/audit" className={button.quiet}>Clear</Link>
        </div>
      </form>

      <div className="flex flex-wrap items-start gap-5">
        <Card aria-label="Entries" className="flex-[3_1_640px] overflow-hidden">
          {items.length === 0 ? (
            <p className="m-0 px-[18px] py-8 text-[14px] text-muted">No entries match these filters.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse text-[14px]">
                <thead>
                  <tr>
                    <th scope="col" className={cx(th, "pl-[18px] pt-3")}>Time (GMT)</th>
                    <th scope="col" className={cx(th, "pt-3")}>Actor</th>
                    <th scope="col" className={cx(th, "pt-3")}>Action</th>
                    <th scope="col" className={cx(th, "pt-3")}>Object</th>
                    <th scope="col" className={cx(th, "pr-[18px] pt-3")}>Entry</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((e) => {
                    const on = e.seq === entrySeq;
                    const cat = auditCategory(e.category);
                    const href = objectHref(e.object_ref);
                    const sameDay = e.occurred_at.slice(0, 10) === isoDate(new Date());
                    return (
                      <tr key={e.seq} className={on ? "bg-selected" : "bg-white"}>
                        <td className={cx(td, "tabular whitespace-nowrap py-[11px] pl-[18px] text-ink-2")}>
                          <time dateTime={e.occurred_at}>{sameDay ? formatTime(e.occurred_at, { seconds: true }) : `${formatDate(e.occurred_at)}, ${formatTime(e.occurred_at, { seconds: true })}`}</time>
                        </td>
                        <td className={cx(td, "py-[11px]")}>
                          <div className="flex flex-col gap-0.5">
                            <span className="font-medium">{e.actor_label}</span>
                            <span className="text-[12px] text-muted">{e.actor_via}</span>
                          </div>
                        </td>
                        <td className={cx(td, "py-[11px]")}>
                          <Chip tone={cat.tone} size="sm">{cat.label}</Chip>{" "}
                          <Link href={hrefWith("/audit", current, { entry: String(e.seq) })} scroll={false} className="ml-1.5 text-ink hover:text-action">
                            {e.action}
                          </Link>
                        </td>
                        <td className={cx(td, "py-[11px] font-mono text-[13px] text-ink-2")}>
                          {href ? <Link href={href}>{e.object_ref}</Link> : e.object_ref}
                        </td>
                        <td className={cx(td, "whitespace-nowrap py-[11px] pr-[18px] font-mono text-[13px]")}>
                          <Link href={hrefWith("/audit", current, { entry: String(e.seq) })} aria-current={on ? "true" : undefined} aria-label={`Open entry ${formatNumber(e.seq)}`} scroll={false} className="inline-flex min-h-11 items-center">
                            {formatNumber(e.seq)}
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {(next_cursor || cursor) && (
            <nav aria-label="Pages" className="flex justify-between gap-2 px-[18px] py-3">
              {cursor ? <Link href={hrefWith("/audit", current, { cursor: undefined })} className={button.small}>Newest entries</Link> : <span />}
              {next_cursor && <Link href={hrefWith("/audit", current, { cursor: next_cursor })} className={button.small}>Older entries</Link>}
            </nav>
          )}
        </Card>

        <aside aria-labelledby="entry-title" className="flex min-w-0 flex-[2_1_340px] flex-col gap-3.5 rounded-[10px] border border-line bg-white px-[22px] py-5">
          {entry ? (
            <EntryDetail entry={entry} />
          ) : (
            <>
              <h2 id="entry-title" className="m-0 text-[18px] font-semibold">Entry detail</h2>
              <p className="m-0 text-[14px] text-muted">{entrySeq ? "That entry could not be loaded." : "Select an entry number to see its detail, its hashes and its signature."}</p>
            </>
          )}
        </aside>
      </div>
    </>
  );
}
