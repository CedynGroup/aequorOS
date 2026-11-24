import type { Metadata } from "next";
import Link from "next/link";
import { ChannelCell, StatusChip } from "@/components/filings";
import { HeatCell, HeatLegend } from "@/components/heatmap";
import { Card, CardTitle, Chip, PageHeader, PageProblem, Stat, button, cx, field, td, th } from "@/components/ui";
import type { AttentionItem, Overview } from "@/lib/api/types";
import { formatDate, formatDateTime, formatMonthYear, formatNumber, formatPercent, formatSeconds, formatShare, formatTime, formatVersion, isoDate, plural } from "@/lib/format";
import { ATTENTION_KIND, HEAT_STATE } from "@/lib/labels";
import { DATE, param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load } from "@/lib/server/load";

export const metadata: Metadata = { title: "Overview" };

function attentionHref(link: AttentionItem["link"]): string {
  switch (link.type) {
    case "submission":
      return `/submissions/${encodeURIComponent(link.ref)}`;
    case "institution":
      return `/institutions/${encodeURIComponent(link.ref)}`;
    case "query":
      return `/queries/${encodeURIComponent(link.ref)}`;
    case "onboarding":
      return `/onboarding?institution=${encodeURIComponent(link.ref)}`;
  }
}

/** The last twelve month-end reporting dates up to the given one. */
function recentCycles(latest: string): string[] {
  const [y, m] = latest.split("-").map(Number) as [number, number];
  return Array.from({ length: 12 }, (_, i) => isoDate(new Date(Date.UTC(y, m - i, 0))));
}

function CyclePicker({ current }: { current: string }) {
  const today = new Date();
  const lastMonthEnd = isoDate(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 0)));
  const cycles = recentCycles(current > lastMonthEnd ? current : lastMonthEnd);
  return (
    <details className="relative">
      <summary className={cx(button.secondary, "cursor-pointer list-none [&::-webkit-details-marker]:hidden")}>Cycle: {formatMonthYear(current)}</summary>
      <ul className="absolute right-0 z-20 m-0 mt-1 max-h-80 w-56 list-none overflow-y-auto rounded-lg border border-line bg-white p-1 shadow-lg">
        {cycles.map((date) => (
          <li key={date}>
            <Link
              href={`/?reporting_date=${date}`}
              aria-current={date === current ? "true" : undefined}
              className={cx("flex min-h-11 items-center rounded-md px-3 text-[14px] no-underline", date === current ? "bg-action-soft font-semibold text-action" : "text-ink hover:bg-panel")}
            >
              {formatMonthYear(date)}
            </Link>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Kpis({ totals }: { totals: Overview["totals"] }) {
  return (
    <section aria-label="Cycle totals" className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3.5">
      <Stat label="Due to date" value={formatNumber(totals.due)} note={`${plural(totals.institutions, "institution")} · ${plural(totals.returns, "return")}`} />
      <Stat label="Received" value={formatNumber(totals.received)} note={`${formatShare(totals.received, totals.due)} of due`} />
      <Stat label="Accepted" value={formatNumber(totals.accepted)} note={`${formatShare(totals.accepted, totals.received)} of received`} tone="ok" />
      <Stat label="Outstanding past due" value={formatNumber(totals.outstanding_past_due)} note="not received by the due date" tone="bad" />
      <Stat label="Filed late" value={formatNumber(totals.filed_late)} note="received after the due date" tone="warn" />
      <Stat label="Open queries" value={formatNumber(totals.open_queries)} note="with institutions or supervisors" tone="query" />
    </section>
  );
}

function Heatmap({ data, institutions }: { data: Overview["heatmap"]; institutions: number }) {
  return (
    <Card aria-labelledby="heatmap-title" className="flex-[2_1_620px] gap-4 px-[22px] py-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle id="heatmap-title">Compliance by institution</CardTitle>
          <span className="text-[13px] text-muted">
            {plural(institutions, "institution")} · the {data.rows.length} with the most outstanding filings first · select a cell to open the filing
          </span>
        </div>
        <HeatLegend states={["on_time", "late", "outstanding", "not_due"]} labels={HEAT_STATE} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-separate border-spacing-1 text-[13px]">
          <thead>
            <tr>
              <th scope="col" className="pb-1.5 pr-2 text-left text-[12px] font-semibold text-muted">
                Institution
              </th>
              {data.return_codes.map((code) => (
                <th key={code} scope="col" className="whitespace-nowrap pb-1.5 font-mono text-[11px] font-medium text-muted">
                  {code}
                </th>
              ))}
              <th scope="col" className="pb-1.5 pl-2 text-right text-[12px] font-semibold text-muted">
                Received
              </th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.institution_code}>
                <th scope="row" className="whitespace-nowrap pr-2 text-left font-medium">
                  <Link href={`/institutions/${row.institution_code}`} className="text-ink no-underline hover:underline">
                    {row.institution_name}
                  </Link>
                </th>
                {data.return_codes.map((code) => {
                  const cell = row.cells.find((c) => c.return_code === code);
                  const state = cell?.state ?? "not_due";
                  const href = cell?.receipt_id ? `/submissions/${cell.receipt_id}` : state === "outstanding" ? `/institutions/${row.institution_code}` : null;
                  return (
                    <td key={code} className="p-0">
                      <HeatCell state={state} href={href} label={`${row.institution_name}, ${code}: ${HEAT_STATE[state].label}`} />
                    </td>
                  );
                })}
                <td className="tabular pl-2 text-right text-ink-2">
                  {row.received} / {row.due}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Attention({ items }: { items: AttentionItem[] }) {
  return (
    <Card aria-labelledby="attention-title" className="gap-3.5 px-[22px] py-5">
      <div className="flex items-baseline justify-between">
        <CardTitle id="attention-title">Needs attention</CardTitle>
        <Link href="/submissions" className="text-[13px] font-medium">
          View all
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="m-0 text-[14px] text-muted">Nothing needs attention in this cycle.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {items.map((item, i) => {
            const kind = ATTENTION_KIND[item.kind];
            return (
              <li key={`${item.link.ref}-${i}`} className="flex flex-col gap-1.5 border-t border-hairline py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone={kind.tone}>{kind.label}</Chip>
                  <span className="text-[12px] text-muted">{item.when}</span>
                </div>
                <Link href={attentionHref(item.link)} className="text-[14px] font-medium leading-[1.4] text-ink no-underline hover:underline">
                  {item.title}
                </Link>
                <span className="text-[13px] leading-[1.4] text-muted">{item.detail}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function ChannelStatus({ channel }: { channel: Overview["channel"] }) {
  const stats = [
    {
      label: "API availability, 30 days",
      value: channel.api_availability_30d === null ? "No API traffic yet" : formatPercent(channel.api_availability_30d, 2),
    },
    {
      label: "Median time to receipt",
      value: channel.median_receipt_seconds === null ? "No filings yet" : formatSeconds(channel.median_receipt_seconds),
    },
    { label: "Filings received today", value: formatNumber(channel.filings_today) },
    { label: "Certificates expiring ≤ 30 days", value: formatNumber(channel.certificates_expiring_30d) },
  ];
  return (
    <Card aria-labelledby="channel-title" className="gap-3 px-[22px] py-5">
      <CardTitle id="channel-title">Channel status</CardTitle>
      <dl className="m-0 grid grid-cols-2 gap-3.5">
        {stats.map((s) => (
          <div key={s.label} className="flex flex-col gap-1">
            <dt className="text-[12px] text-muted">{s.label}</dt>
            <dd className="tabular m-0 text-[18px] font-semibold">{s.value}</dd>
          </div>
        ))}
      </dl>
      <span className="text-[12px] text-muted">Every filing is received through the published API v1, whichever system produced it.</span>
    </Card>
  );
}

function Latest({ rows }: { rows: Overview["latest"] }) {
  return (
    <Card aria-labelledby="latest-title" className="gap-3.5 px-[22px] py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <CardTitle id="latest-title">Latest submissions</CardTitle>
        <Link href="/submissions" className="text-[13px] font-medium">
          Open inbox
        </Link>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-[14px]">
          <thead>
            <tr>
              <th scope="col" className={cx(th, "pl-0")}>Received</th>
              <th scope="col" className={th}>Institution</th>
              <th scope="col" className={th}>Return</th>
              <th scope="col" className={th}>Reporting date</th>
              <th scope="col" className={th}>Version</th>
              <th scope="col" className={th}>Channel · software</th>
              <th scope="col" className={th}>Status</th>
              <th scope="col" className={cx(th, "pr-0")}>Receipt</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.receipt_id}>
                <td className={cx(td, "tabular whitespace-nowrap pl-0 text-ink-2")}>
                  <time dateTime={row.received_at} title={formatDateTime(row.received_at, { seconds: true, zone: true })}>
                    {formatTime(row.received_at)}
                  </time>
                </td>
                <td className={cx(td, "font-medium")}>{row.institution_name}</td>
                <td className={cx(td, "font-mono text-[13px]")}>{row.return_code}</td>
                <td className={cx(td, "whitespace-nowrap text-ink-2")}>{formatDate(row.reporting_date)}</td>
                <td className={cx(td, "text-ink-2")}>{formatVersion(row.version)}</td>
                <td className={cx(td, "whitespace-nowrap text-ink-2")}>
                  <ChannelCell channel={row.channel} software={row.software_declared} />
                </td>
                <td className={td}>
                  <StatusChip state={row.status} />
                </td>
                <td className={cx(td, "pr-0")}>
                  <Link href={`/submissions/${row.receipt_id}`} className="font-mono text-[13px]">
                    {row.receipt_id}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default async function OverviewPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const requested = param(params, "reporting_date");
  const reportingDate = requested && DATE.test(requested) ? requested : undefined;
  const api = await getApi();
  const result = await load(api.overview(reportingDate ? { reporting_date: reportingDate } : {}));
  const crumbs = [{ label: "Supervision" }, { label: "Overview" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Filing compliance" kind={result.kind} message={result.message} />;
  const data = result.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        crumbs={crumbs}
        title="Filing compliance"
        subtitle={`${formatMonthYear(data.reporting_date)} reporting cycle · updated ${formatDateTime(new Date(), { zone: true })}`}
        actions={
          <>
            <form action="/submissions" role="search" className="flex">
              <label htmlFor="overview-search" className="sr-only">
                Search
              </label>
              <input id="overview-search" name="q" type="search" placeholder="Search institution, return or receipt" className={cx(field.input, "w-[300px] max-w-full px-3.5")} />
            </form>
            <CyclePicker current={data.reporting_date} />
            <a href={`/export/cycle?reporting_date=${data.reporting_date}`} className={button.primary} download>
              Export cycle report
            </a>
          </>
        }
      />
      <Kpis totals={data.totals} />
      <div className="flex flex-wrap items-start gap-5">
        <Heatmap data={data.heatmap} institutions={data.totals.institutions} />
        <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-5">
          <Attention items={data.attention} />
          <ChannelStatus channel={data.channel} />
        </div>
      </div>
      <Latest rows={data.latest} />
    </div>
  );
}
