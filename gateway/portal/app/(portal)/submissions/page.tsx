import type { Metadata } from "next";
import Link from "next/link";
import { ChannelCell, StatusChip, ValidationCell } from "@/components/filings";
import { SelectableForm } from "@/components/selectable-form";
import { Card, PageHeader, PageProblem, Unavailable, button, cx, field, td, th } from "@/components/ui";
import type { SubmissionCountKey, SubmissionListParams } from "@/lib/api/types";
import { formatDate, formatDateTime, formatMonthYear, formatNumber, formatVersion, isoDate, parseInstant } from "@/lib/format";
import { DATE, INSTITUTION_CODE, hrefWith, oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load, optional } from "@/lib/server/load";

export const metadata: Metadata = { title: "Submissions" };

const TABS: { key: SubmissionCountKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "needs_review", label: "Needs review" },
  { key: "validating", label: "Validating" },
  { key: "rejected", label: "Rejected" },
  { key: "queried", label: "Queried" },
  { key: "accepted", label: "Accepted" },
];

const PAGE_SIZE = 25;
const FIRST = "~";

function isMonthEnd(date: string): boolean {
  const d = parseInstant(date);
  if (!d) return false;
  return new Date(d.getTime() + 86_400_000).getUTCDate() === 1;
}

function recentMonthEnds(count: number): string[] {
  const today = new Date();
  return Array.from({ length: count }, (_, i) => isoDate(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - i, 0))));
}

export default async function SubmissionsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const tab = oneOf(param(params, "status"), TABS.map((t) => t.key), "all");
  const filters = {
    q: param(params, "q"),
    return_code: param(params, "return_code"),
    institution_code: param(params, "institution_code"),
    reporting_date: param(params, "reporting_date"),
    channel: param(params, "channel"),
  };
  if (filters.institution_code && !INSTITUTION_CODE.test(filters.institution_code)) filters.institution_code = undefined;
  if (filters.reporting_date && !DATE.test(filters.reporting_date)) filters.reporting_date = undefined;
  if (filters.channel && !["api", "portal_upload"].includes(filters.channel)) filters.channel = undefined;
  const cursor = param(params, "cursor");
  const trail = (param(params, "trail") ?? "").split(",").filter(Boolean);
  const limitParam = Number(param(params, "limit"));
  const limit = Number.isInteger(limitParam) && limitParam > 0 && limitParam <= 200 ? limitParam : PAGE_SIZE;

  const api = await getApi();
  const query: SubmissionListParams = { ...filters, status: tab === "all" ? undefined : tab, cursor, limit };
  const [result, returns, institutions] = await Promise.all([load(api.listSubmissions(query)), optional(api.listReturns()), optional(api.listInstitutions())]);
  const crumbs = [{ label: "Supervision" }, { label: "Submissions" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Submissions" kind={result.kind} message={result.message} />;
  const { items, counts, next_cursor } = result.data;

  const current: Record<string, string | undefined> = { ...filters, status: tab === "all" ? undefined : tab, limit: limit === PAGE_SIZE ? undefined : String(limit) };
  const start = trail.length * limit + 1;
  const end = trail.length * limit + items.length;
  const total = counts[tab];
  const previous = trail.length > 0 ? trail[trail.length - 1] : undefined;
  const prevHref = previous !== undefined ? hrefWith("/submissions", current, { cursor: previous === FIRST ? undefined : previous, trail: trail.slice(0, -1).join(",") || undefined }) : null;
  const nextHref = next_cursor ? hrefWith("/submissions", current, { cursor: next_cursor, trail: [...trail, cursor ?? FIRST].join(",") }) : null;
  const dateLabel = filters.reporting_date ? (isMonthEnd(filters.reporting_date) ? ` for the ${formatMonthYear(filters.reporting_date)} cycle` : ` for reporting date ${formatDate(filters.reporting_date)}`) : "";
  const reportingDates = recentMonthEnds(12);
  if (filters.reporting_date && !reportingDates.includes(filters.reporting_date)) reportingDates.unshift(filters.reporting_date);
  const exportParams = new URLSearchParams(Object.entries({ ...filters, status: tab === "all" ? undefined : tab }).filter((e): e is [string, string] => Boolean(e[1])));

  return (
    <>
      <PageHeader
        crumbs={crumbs}
        title="Submissions"
        subtitle="Every filing received through API v1 and the institution portal, newest first"
        actions={
          <>
            <Unavailable reason="Saved views are not offered by the gateway yet">Saved views</Unavailable>
            <a href={`/export/submissions${exportParams.size ? `?${exportParams}` : ""}`} className={button.secondary} download>
              Export CSV
            </a>
          </>
        }
      />

      <nav aria-label="Status" className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => {
          const on = t.key === tab;
          return (
            <Link
              key={t.key}
              href={hrefWith("/submissions", { ...current, status: undefined }, { status: t.key === "all" ? undefined : t.key })}
              aria-current={on ? "page" : undefined}
              className={cx(
                "-mb-px inline-flex min-h-11 items-center gap-2 border-b-2 px-3.5 text-[14px] no-underline",
                on ? "border-action font-semibold text-ink hover:text-ink" : "border-transparent font-medium text-muted hover:text-ink",
              )}
            >
              {t.label}
              <span className={cx("rounded-full px-2 py-px text-[12px] font-semibold", on ? "bg-action-soft text-action" : "bg-neutral-soft text-muted")}>{formatNumber(counts[t.key])}</span>
            </Link>
          );
        })}
      </nav>

      <form aria-label="Filters" action="/submissions" method="get" className="flex flex-wrap items-end gap-3 rounded-[10px] border border-line bg-white px-[18px] py-4">
        {tab !== "all" && <input type="hidden" name="status" value={tab} />}
        <div className="flex flex-[2_1_260px] flex-col gap-1.5">
          <label htmlFor="inbox-search" className={field.label}>
            Search
          </label>
          <input id="inbox-search" name="q" type="search" defaultValue={filters.q} placeholder="Institution, receipt or return code" className={field.input} />
        </div>
        <div className="flex flex-[1_1_160px] flex-col gap-1.5">
          <label htmlFor="f-return" className={field.label}>
            Return
          </label>
          <select id="f-return" name="return_code" defaultValue={filters.return_code ?? ""} className={field.select}>
            <option value="">All returns</option>
            {(returns?.items ?? []).map((r) => (
              <option key={r.code} value={r.code}>
                {r.code} — {r.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_160px] flex-col gap-1.5">
          <label htmlFor="f-institution" className={field.label}>
            Institution
          </label>
          <select id="f-institution" name="institution_code" defaultValue={filters.institution_code ?? ""} className={field.select}>
            <option value="">All institutions</option>
            {(institutions?.items ?? []).map((i) => (
              <option key={i.code} value={i.code}>
                {i.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_160px] flex-col gap-1.5">
          <label htmlFor="f-period" className={field.label}>
            Reporting date
          </label>
          <select id="f-period" name="reporting_date" defaultValue={filters.reporting_date ?? ""} className={field.select}>
            <option value="">All reporting dates</option>
            {reportingDates.map((d) => (
              <option key={d} value={d}>
                {formatDate(d)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-[1_1_160px] flex-col gap-1.5">
          <label htmlFor="f-channel" className={field.label}>
            Channel
          </label>
          <select id="f-channel" name="channel" defaultValue={filters.channel ?? ""} className={field.select}>
            <option value="">All channels</option>
            <option value="api">API</option>
            <option value="portal_upload">Institution portal upload</option>
          </select>
        </div>
        <div className="flex gap-2">
          <button type="submit" className={button.secondary}>
            Apply filters
          </button>
          <Link href={tab === "all" ? "/submissions" : `/submissions?status=${tab}`} className={button.quiet}>
            Clear
          </Link>
        </div>
      </form>

      <Card aria-label="Submissions">
        <SelectableForm
          action="/export/submissions"
          actions={
            <>
              <Unavailable size="sm" reason="Assigning to another supervisor needs a staff directory the gateway does not offer yet; open a filing to assign it to yourself">
                Assign
              </Unavailable>
              <button type="submit" className={button.small}>
                Export selected
              </button>
            </>
          }
        >
          {items.length === 0 ? (
            <p className="m-0 px-[18px] py-8 text-[14px] text-muted">No submissions match these filters.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1080px] border-collapse text-[14px]">
                <thead>
                  <tr>
                    <th scope="col" className={cx(th, "w-7 py-0 pl-[18px] pr-2")}>
                      <label className="flex min-h-11 items-center">
                        <input type="checkbox" data-select-all="" aria-label="Select all on this page" className="size-[18px]" />
                      </label>
                    </th>
                    <th scope="col" className={th}>Received</th>
                    <th scope="col" className={th}>Institution</th>
                    <th scope="col" className={th}>Return</th>
                    <th scope="col" className={th}>Reporting date</th>
                    <th scope="col" className={th}>Ver.</th>
                    <th scope="col" className={th}>Channel</th>
                    <th scope="col" className={th}>Validation</th>
                    <th scope="col" className={th}>Status</th>
                    <th scope="col" className={th}>Reviewer</th>
                    <th scope="col" className={cx(th, "pr-[18px]")}>Receipt</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.receipt_id} className="bg-white has-checked:bg-selected">
                      <td className={cx(td, "py-0 pl-[18px] pr-2 align-middle")}>
                        <label className="flex min-h-11 items-center">
                          <input type="checkbox" name="receipt" value={row.receipt_id} aria-label={`Select ${row.receipt_id}`} className="size-[18px]" />
                        </label>
                      </td>
                      <td className={cx(td, "tabular whitespace-nowrap text-ink-2")}>
                        <time dateTime={row.received_at}>{formatDateTime(row.received_at, { year: false })}</time>
                      </td>
                      <td className={cx(td, "whitespace-nowrap font-medium")}>{row.institution_name}</td>
                      <td className={cx(td, "min-w-[220px]")}>
                        <div className="flex flex-col gap-0.5">
                          <span className="font-mono text-[13px] font-medium">{row.return_code}</span>
                          <span className="text-[12px] text-muted">{row.return_name}</span>
                        </div>
                      </td>
                      <td className={cx(td, "whitespace-nowrap text-ink-2")}>{formatDate(row.reporting_date)}</td>
                      <td className={cx(td, "text-ink-2")}>{formatVersion(row.version)}</td>
                      <td className={cx(td, "whitespace-nowrap text-ink-2")}>
                        <ChannelCell channel={row.channel} software={row.software_declared} />
                      </td>
                      <td className={cx(td, "whitespace-nowrap")}>
                        <ValidationCell row={row} />
                      </td>
                      <td className={td}>
                        <StatusChip state={row.status} />
                      </td>
                      <td className={cx(td, "whitespace-nowrap text-ink-2")}>{row.reviewer_name ?? "—"}</td>
                      <td className={cx(td, "pr-[18px]")}>
                        <Link href={`/submissions/${row.receipt_id}`} className="font-mono text-[13px]">
                          {row.receipt_id}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SelectableForm>
        <div className="flex flex-wrap items-center justify-between gap-2.5 px-[18px] py-3.5">
          <span className="text-[13px] text-muted">
            {items.length > 0 ? `Showing ${formatNumber(start)}–${formatNumber(end)} of ${formatNumber(Math.max(total, end))}${dateLabel}` : `No submissions${dateLabel}`}
          </span>
          <nav aria-label="Pages" className="flex gap-2">
            {prevHref ? (
              <Link href={prevHref} className={button.small}>
                Previous
              </Link>
            ) : (
              <button type="button" disabled className={button.small}>
                Previous
              </button>
            )}
            {nextHref ? (
              <Link href={nextHref} className={button.small}>
                Next
              </Link>
            ) : (
              <button type="button" disabled className={button.small}>
                Next
              </button>
            )}
          </nav>
        </div>
      </Card>
    </>
  );
}
