import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { HeatCell, HeatLegend } from "@/components/heatmap";
import { Breadcrumb, Card, CardTitle, Chip, PageProblem, Row, Stat, button, field } from "@/components/ui";
import type { HeatState, InstitutionDetail } from "@/lib/api/types";
import { formatDate, formatDayMonth, formatMonthName, formatPercent, plural } from "@/lib/format";
import { HISTORY_STATE, OBLIGATION_STATUS, filesByLabel, flagLabel, institutionTypeLabel } from "@/lib/labels";
import { capabilities } from "@/lib/roles";
import { INSTITUTION_CODE } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";
import { load } from "@/lib/server/load";
import { addNote } from "../../actions";

type Props = { params: Promise<{ code: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: `Institution ${(await params).code}` };
}

function obligationDetail(o: InstitutionDetail["obligations"][number]): string {
  const due = formatDayMonth(o.due_at);
  if (o.status === "overdue") return `${formatDate(o.reporting_date)} · was due ${due}`;
  if (o.status === "filed" || o.status === "filed_late") return `${formatDate(o.reporting_date)} · filed`;
  return `${formatDate(o.reporting_date)} · due ${due}`;
}

export default async function InstitutionPage({ params }: Props) {
  const code = decodeURIComponent((await params).code);
  if (!INSTITUTION_CODE.test(code)) notFound();
  const api = await getApi();
  const [result, me] = await Promise.all([load(api.getInstitution(code)), getMe()]);
  const crumbs = [{ label: "Institutions", href: "/institutions" }, { label: code }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title={`Institution ${code}`} kind={result.kind} message={result.message} />;
  const d = result.data;
  const s = d.stats;
  const supervise = capabilities.supervise(me.roles);
  const latestDate = d.history.rows.flatMap((r) => r.cells.map((c) => c.reporting_date)).sort().at(-1);
  const states = new Set<HeatState>(d.history.rows.flatMap((r) => r.cells.map((c) => c.state)));
  const legend = (["on_time", "late", "outstanding", "not_due"] as HeatState[]).filter((st) => st !== "not_due" || states.has(st));
  const api_client = d.credentials.api_client;

  return (
    <>
      <header className="flex flex-col gap-3">
        <Breadcrumb items={[{ label: "Institutions", href: "/institutions" }, { label: d.name }]} />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="m-0 text-[28px] font-semibold tracking-[-0.01em]">{d.name}</h1>
            <div className="flex flex-wrap gap-2">
              <Chip tone="grey">{institutionTypeLabel(d.type, "long")}</Chip>
              <Chip tone="grey">Institution code {d.code}</Chip>
              <Chip tone="grey">{filesByLabel(d.channel)}</Chip>
              {d.flags.map((flag) => (
                <Chip key={flag} tone="red">{flagLabel(flag)}</Chip>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <a href={`/export/institution/${d.code}`} className={button.secondary} download>Export filing record</a>
            {supervise && (
              <Link href={`/queries?new=1&institution_code=${d.code}`} className={button.primary}>Raise query</Link>
            )}
          </div>
        </div>
      </header>

      <section aria-label="Filing summary" className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3.5">
        <Stat label="On time, 12 months" value={formatPercent(s.on_time_12m)} note={`peer median ${formatPercent(s.peer_median_on_time_12m)}`} tone={s.on_time_12m < s.peer_median_on_time_12m ? "warn" : "ink"} />
        <Stat label="Outstanding now" value={s.outstanding} note={latestDate ? `${formatMonthName(latestDate)} cycle` : "current cycle"} tone={s.outstanding > 0 ? "bad" : "ink"} />
        <Stat label="Filed late this year" value={s.late_this_year} note={`of ${plural(s.filings_this_year, "filing")}`} tone={s.late_this_year > 0 ? "warn" : "ink"} />
        <Stat label="Open queries" value={s.open_queries} note="with this institution" tone="query" />
        <Stat label="Last filing" value={s.last_filing ? formatDayMonth(s.last_filing.at) : "None yet"} note={s.last_filing?.label} />
      </section>

      <div className="flex flex-wrap items-start gap-5">
        <Card aria-labelledby="history-title" className="flex-[2_1_620px] gap-3.5 px-[22px] py-5">
          <div className="flex flex-wrap items-start justify-between gap-2.5">
            <div className="flex flex-col gap-1">
              <CardTitle id="history-title">Filing record, last {d.history.months.length} months</CardTitle>
              <span className="text-[13px] text-muted">Monthly returns by reporting date · select a cell to open the filing</span>
            </div>
            <HeatLegend states={legend} labels={HISTORY_STATE} />
          </div>
          {d.history.rows.length === 0 ? (
            <p className="m-0 text-[14px] text-muted">No filings yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-separate border-spacing-1 text-[13px]">
                <thead>
                  <tr>
                    <th scope="col" className="pb-1.5 pr-2 text-left text-[12px] font-semibold text-muted">Return</th>
                    {d.history.months.map((m) => (
                      <th key={m} scope="col" className="whitespace-nowrap pb-1.5 text-[11px] font-medium text-muted">{m}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.history.rows.map((row) => (
                    <tr key={row.return_code}>
                      <th scope="row" className="whitespace-nowrap pr-2 text-left font-mono text-[12px] font-medium">{row.return_code}</th>
                      {row.cells.map((cell, i) => (
                        <td key={cell.reporting_date} className="p-0">
                          <HeatCell
                            state={cell.state}
                            href={cell.receipt_id ? `/submissions/${cell.receipt_id}` : null}
                            label={`${row.return_code}, ${d.history.months[i] ?? formatDate(cell.reporting_date)}: ${HISTORY_STATE[cell.state].label}`}
                            minWidth={36}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-5">
          <Card aria-labelledby="due-title" className="gap-2.5 px-[22px] py-5">
            <CardTitle id="due-title">Obligations, next 30 days</CardTitle>
            {d.obligations.length === 0 ? (
              <p className="m-0 text-[14px] text-muted">Nothing is due in the next 30 days.</p>
            ) : (
              <ul className="m-0 list-none p-0">
                {d.obligations.map((o) => {
                  const status = OBLIGATION_STATUS[o.status];
                  return (
                    <li key={`${o.return_code}-${o.reporting_date}`} className="flex items-center justify-between gap-3 border-t border-hairline py-2.5">
                      <span className="flex flex-col gap-0.5">
                        {o.receipt_id ? (
                          <Link href={`/submissions/${o.receipt_id}`} className="font-mono text-[13px] font-medium">{o.return_code}</Link>
                        ) : (
                          <span className="font-mono text-[13px] font-medium">{o.return_code}</span>
                        )}
                        <span className="text-[12px] text-muted">{obligationDetail(o)}</span>
                      </span>
                      <Chip tone={status.tone}>{status.label}</Chip>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card aria-labelledby="contacts-title" className="gap-3 px-[22px] py-5">
            <CardTitle id="contacts-title">Contacts and credentials</CardTitle>
            <dl className="m-0 flex flex-col gap-2.5 text-[14px]">
              {d.contacts.map((c) => (
                <Row key={c.role} label={c.role}>{c.name}</Row>
              ))}
              <Row label="Portal users">
                {d.credentials.portal_users} active · MFA {d.credentials.mfa ? "on" : "off"}
              </Row>
              <Row label="API client">{api_client ? `${api_client.client_id} · expires ${formatDate(api_client.certificate_not_after)}` : "Not yet issued"}</Row>
              {d.credentials.signing_keys.length > 0 && (
                <Row label="Signing keys">
                  <span className="font-mono text-[13px]">{d.credentials.signing_keys.map((k) => `${k.kid} (${k.status})`).join(", ")}</span>
                </Row>
              )}
              <Row label="Relationship supervisor">{d.supervisor_name ?? "Not assigned"}</Row>
            </dl>
            <Link href={`/onboarding?institution=${d.code}`} className="text-[13px] font-medium">View on the channel register</Link>
          </Card>

          <Card aria-labelledby="notes-title" className="gap-2.5 px-[22px] py-5">
            <div className="flex items-baseline justify-between gap-2">
              <CardTitle id="notes-title">Supervisory notes</CardTitle>
              <span className="text-[12px] text-muted">Internal · never shown to the institution</span>
            </div>
            {d.notes.length === 0 ? (
              <p className="m-0 text-[14px] text-muted">No notes yet.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
                {d.notes.map((n) => (
                  <li key={n.id} className="text-[14px] leading-[1.55] text-ink-2">
                    {n.body} — {n.author_name}, {formatDayMonth(n.created_at)}
                  </li>
                ))}
              </ul>
            )}
            {supervise && (
              <ActionForm action={addNote.bind(null, d.code)} label="Add a supervisory note" className="flex flex-col gap-2 border-t border-hairline pt-3">
                <label htmlFor="note-body" className={field.label}>Add a note</label>
                <textarea id="note-body" name="body" rows={3} required maxLength={4000} className={field.textarea} />
                <div>
                  <SubmitButton variant="secondary" pendingLabel="Saving…">Add note</SubmitButton>
                </div>
              </ActionForm>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
