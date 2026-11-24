import Link from "next/link";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { Notice } from "@/components/notice";
import { Card, Chip, PageHeader, PageProblem, Row, Unavailable, button, cx, field, td, th } from "@/components/ui";
import type { ReturnDefinitionDetail, ReturnDraft } from "@/lib/api/types";
import { formatDate, formatDayMonth, shortHash } from "@/lib/format";
import { CADENCE, cadenceLabel } from "@/lib/labels";
import { noticeFrom } from "@/lib/notice";
import { capabilities } from "@/lib/roles";
import { oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";
import { load, optional } from "@/lib/server/load";
import { decideReturnVersion, draftReturnVersion } from "../actions";

const ORDINAL = ["First", "Second", "Third", "Fourth"];

function draftStatus(draft: ReturnDraft): string {
  const approved = draft.approvals.filter((a) => a.step === "approved").length;
  const pending = draft.approvals.filter((a) => a.step === "pending").length;
  if (pending === 0) return "Approved";
  return `Awaiting ${(ORDINAL[approved] ?? "next").toLowerCase()} approval`;
}

function nextVersion(current: string): string {
  const [year, minor] = current.split(".");
  return `${year}.${Number(minor ?? 0) + 1}`;
}

function DraftPanel({ detail, canDecide }: { detail: ReturnDefinitionDetail; canDecide: boolean }) {
  const draft = detail.draft as ReturnDraft;
  const pending = draft.approvals.filter((a) => a.step === "pending").length;
  const ordinals = draft.approvals.map((a, i) => (a.step === "drafted" ? null : (ORDINAL[draft.approvals.slice(0, i).filter((b) => b.step !== "drafted").length] ?? "Next")));
  return (
    <>
      <section aria-labelledby="draft-title" className="flex flex-col gap-3.5 rounded-[10px] border border-line bg-white px-[22px] py-5">
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-[13px] text-muted">{detail.code}</span>
          <h2 id="draft-title" className="m-0 text-[19px] font-semibold">
            {detail.name} — draft {draft.version}
          </h2>
          <Chip tone="amber" className="self-start">{draftStatus(draft)}</Chip>
        </div>
        <dl className="m-0 flex flex-col gap-2.5 text-[14px]">
          <Row label="Change">{draft.change_summary}</Row>
          <Row label="First reporting date">{formatDate(draft.first_reporting_date)}</Row>
          <Row label="Notice to institutions">{draft.notice_days} days, from approval</Row>
          <Row label="Sandbox testing">Opens on approval</Row>
          <Row label="Breaking for API v1">{draft.breaking ? "Yes, ships only as API v2" : "No, additive change"}</Row>
        </dl>
        <div className="flex flex-col gap-2">
          <span className="text-[13px] font-semibold text-ink-2" id="approvals-title">Approvals</span>
          <ol aria-labelledby="approvals-title" className="m-0 flex list-none flex-col gap-2 p-0">
            {draft.approvals.map((a, i) => {
              if (a.step === "drafted") {
                return (
                  <li key={i} className="flex justify-between gap-2.5 rounded-lg bg-canvas px-3 py-2.5 text-[14px]">
                    <span>Drafted by <strong className="font-semibold">{a.by ?? "unknown"}</strong></span>
                    <span className="font-semibold text-ok">{formatDayMonth(a.at)}</span>
                  </li>
                );
              }
              const ordinal = ordinals[i] ?? "Next";
              return a.step === "approved" ? (
                <li key={i} className="flex justify-between gap-2.5 rounded-lg bg-canvas px-3 py-2.5 text-[14px]">
                  <span>Approved by <strong className="font-semibold">{a.by ?? "unknown"}</strong>, {a.role_label}</span>
                  <span className="font-semibold text-ok">{formatDayMonth(a.at)}</span>
                </li>
              ) : (
                <li key={i} className="flex justify-between gap-2.5 rounded-lg border border-dashed border-field px-3 py-2.5 text-[14px]">
                  <span>{ordinal} approver, {a.role_label}</span>
                  <span className="font-semibold text-warn">Pending</span>
                </li>
              );
            })}
          </ol>
        </div>
        {canDecide ? (
          <ActionForm action={decideReturnVersion.bind(null, detail.code, draft.version)} label="Decide on this draft" className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-2">
              <Unavailable reason="Comparing versions is not offered by the supervisor API yet">Compare with {detail.current_version}</Unavailable>
              <details className="group">
                <summary className={cx(button.secondary, "cursor-pointer list-none [&::-webkit-details-marker]:hidden")}>Return to drafter</summary>
                <div className="mt-2 flex w-[min(340px,calc(100vw-64px))] flex-col gap-2">
                  <label htmlFor="return-reason" className={field.label}>Why it is being returned</label>
                  <textarea id="return-reason" name="reason" rows={3} required className={field.textarea} />
                  <SubmitButton variant="secondary" intent="return" pendingLabel="Returning…">Return the draft</SubmitButton>
                </div>
              </details>
              <SubmitButton intent="approve" skipValidation pendingLabel="Approving…">{pending <= 1 ? "Approve and publish" : "Approve"}</SubmitButton>
            </div>
            <span className="text-[12px] leading-[1.5] text-muted">The drafter and the two approvers must be three different people; the gateway refuses anything else.</span>
          </ActionForm>
        ) : (
          <p className="m-0 text-[13px] text-muted">Return definitions are approved by returns policy officers.</p>
        )}
      </section>

      <section aria-labelledby="publish-title" className="flex flex-col gap-2.5 rounded-[10px] border border-line bg-white px-[22px] py-5">
        <h2 id="publish-title" className="m-0 text-[17px] font-semibold">What publishing does</h2>
        <ol className="m-0 pl-5 text-[14px] leading-[1.6] text-ink-2">
          <li>
            Adds version {draft.version} to the API (<span className="font-mono text-[13px]">GET /v1/returns/{detail.code}</span>) and the documentation site.
          </li>
          <li>Sends the {draft.notice_days}-day notice to every institution that files {detail.code}.</li>
          <li>Keeps {detail.current_version} valid for reporting dates before {formatDate(draft.first_reporting_date)}.</li>
          <li>Records both approvals and the schema digest in the audit log.</li>
        </ol>
      </section>
    </>
  );
}

function VersionsPanel({ detail, canDraft }: { detail: ReturnDefinitionDetail; canDraft: boolean }) {
  return (
    <section aria-labelledby="versions-title" className="flex flex-col gap-3.5 rounded-[10px] border border-line bg-white px-[22px] py-5">
      <div className="flex flex-col gap-1.5">
        <span className="font-mono text-[13px] text-muted">{detail.code}</span>
        <h2 id="versions-title" className="m-0 text-[19px] font-semibold">{detail.name}</h2>
        <span className="text-[13px] text-muted">{cadenceLabel(detail.cadence)} · current version {detail.current_version} · no draft awaiting approval</span>
      </div>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {detail.versions.map((v) => (
          <li key={v.version} className="flex flex-col gap-1 rounded-lg bg-canvas px-3 py-2.5 text-[14px]">
            <span className="flex items-center justify-between gap-2">
              <span className="font-mono font-semibold">{v.version}</span>
              <Chip tone={v.status === "published" ? "green" : v.status === "draft" ? "amber" : "muted"}>{v.status === "published" ? "Published" : v.status === "draft" ? "Draft" : "Retired"}</Chip>
            </span>
            <span className="text-[13px] text-muted">
              Reporting dates {formatDate(v.first_reporting_date)} {v.last_reporting_date ? `to ${formatDate(v.last_reporting_date)}` : "onwards"} · {v.rule_count} rules
            </span>
            <span className="text-[12px] text-muted">
              Schema <span className="font-mono">{shortHash(v.schema_sha256)}</span>
              {v.published_at && ` · published ${formatDate(v.published_at)}`}
            </span>
          </li>
        ))}
      </ol>
      {canDraft && (
        <Link href={`/returns/${detail.code}?draft=new`} className={cx(button.secondary, "self-start")}>Draft version {nextVersion(detail.current_version)}</Link>
      )}
    </section>
  );
}

function DraftForm({ detail }: { detail: ReturnDefinitionDetail }) {
  return (
    <section aria-labelledby="new-draft-title" className="flex flex-col gap-3 rounded-[10px] border border-line bg-white px-[22px] py-5">
      <div className="flex flex-col gap-1">
        <span className="font-mono text-[13px] text-muted">{detail.code}</span>
        <h2 id="new-draft-title" className="m-0 text-[19px] font-semibold">Draft a new version</h2>
        <span className="text-[13px] text-muted">Two other officers must approve it before it is published.</span>
      </div>
      <ActionForm action={draftReturnVersion.bind(null, detail.code)} label="Draft a new version" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-version" className={field.label}>Version</label>
          <input id="d-version" name="version" required defaultValue={nextVersion(detail.current_version)} pattern="\d{4}\.\d{1,3}" className={cx(field.input, "font-mono")} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-summary" className={field.label}>What changes</label>
          <input id="d-summary" name="change_summary" required maxLength={500} className={field.input} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-first" className={field.label}>First reporting date</label>
          <input id="d-first" name="first_reporting_date" type="date" required className={field.input} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-notice" className={field.label}>Notice to institutions, in days</label>
          <input id="d-notice" name="notice_days" type="number" min={0} max={365} required className={field.input} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-schema" className={field.label}>Schema (JSON Schema)</label>
          <textarea id="d-schema" name="schema" rows={6} required spellCheck={false} className={cx(field.textarea, "font-mono text-[13px]")} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="d-rules" className={field.label}>Validation rules (JSON list)</label>
          <textarea id="d-rules" name="rules" rows={6} required spellCheck={false} className={cx(field.textarea, "font-mono text-[13px]")} />
        </div>
        <div className="flex flex-wrap gap-2">
          <SubmitButton pendingLabel="Saving…">Save draft</SubmitButton>
          <Link href={`/returns/${detail.code}`} className={button.quiet}>Cancel</Link>
        </div>
      </ActionForm>
    </section>
  );
}

export async function ReturnsView({ params, selected }: { params: SearchParams; selected: string | null }) {
  const q = param(params, "q")?.toLowerCase();
  const cadence = oneOf(param(params, "cadence"), ["", ...Object.keys(CADENCE)], "");
  const status = oneOf(param(params, "state"), ["", "published", "draft_pending"], "");
  const api = await getApi();
  const [list, me] = await Promise.all([load(api.listReturns()), getMe()]);
  const crumbs = [{ label: "Configuration" }, { label: "Return definitions" }];
  if (!list.ok) return <PageProblem crumbs={crumbs} title="Return definitions" kind={list.kind} message={list.message} />;
  const focus = selected ?? list.data.items.find((r) => r.status === "draft_pending")?.code ?? null;
  const detail = focus ? (selected ? await load(api.getReturn(focus)) : await optional(api.getReturn(focus)).then((d) => (d ? { ok: true as const, data: d } : null))) : null;
  const canDecide = capabilities.decideReturns(me.roles);
  const canDraft = capabilities.draftReturns(me.roles);
  const drafting = Boolean(selected && canDraft && param(params, "draft") === "new" && detail?.ok && !detail.data.draft);
  const notice = noticeFrom(params, selected ? `/returns/${selected}` : "/returns");
  const items = list.data.items.filter(
    (r) => (!q || r.code.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)) && (!cadence || r.cadence === cadence) && (!status || r.status === status),
  );
  const filterQuery = new URLSearchParams(Object.entries({ q: param(params, "q"), cadence, state: status }).filter((e): e is [string, string] => Boolean(e[1]))).toString();

  return (
    <>
      {notice && <Notice {...notice} />}
      <PageHeader
        crumbs={crumbs}
        title="Return definitions"
        subtitle="The returns institutions must file, their schemas, validation rules and due dates. Every change needs two approvers."
        actions={
          canDraft && focus ? (
            <Link href={`/returns/${focus}?draft=new`} className={button.primary}>Draft a new version</Link>
          ) : undefined
        }
      />
      <div className="flex flex-wrap items-start gap-5">
        <Card aria-label="Definitions" className="flex-[3_1_640px]">
          <form role="search" method="get" action={selected ? `/returns/${selected}` : "/returns"} className="flex flex-wrap items-end gap-3 border-b border-hairline px-[18px] py-4">
            <div className="flex flex-[2_1_220px] flex-col gap-1.5">
              <label htmlFor="def-search" className={field.label}>Search</label>
              <input id="def-search" name="q" type="search" defaultValue={param(params, "q")} placeholder="Code or name" className={field.input} />
            </div>
            <div className="flex flex-[1_1_160px] flex-col gap-1.5">
              <label htmlFor="def-cadence" className={field.label}>Cadence</label>
              <select id="def-cadence" name="cadence" defaultValue={cadence} className={field.select}>
                <option value="">All cadences</option>
                {Object.entries(CADENCE).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-[1_1_160px] flex-col gap-1.5">
              <label htmlFor="def-status" className={field.label}>Status</label>
              <select id="def-status" name="state" defaultValue={status} className={field.select}>
                <option value="">All</option>
                <option value="published">Published</option>
                <option value="draft_pending">Draft pending approval</option>
              </select>
            </div>
            <button type="submit" className={button.secondary}>Apply</button>
          </form>
          {items.length === 0 ? (
            <p className="m-0 px-[18px] py-8 text-[14px] text-muted">No return definitions match these filters.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] border-collapse text-[14px]">
                <thead>
                  <tr>
                    <th scope="col" className={cx(th, "pl-[18px]")}>Code</th>
                    <th scope="col" className={th}>Name</th>
                    <th scope="col" className={th}>Cadence</th>
                    <th scope="col" className={th}>Version</th>
                    <th scope="col" className={th}>Due date rule</th>
                    <th scope="col" className={th}>Rules</th>
                    <th scope="col" className={cx(th, "pr-[18px]")}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((r) => {
                    const on = r.code === focus;
                    const draftVersion = on && detail?.ok ? detail.data.draft?.version : undefined;
                    return (
                      <tr key={r.code} className={on ? "bg-selected" : "bg-white"}>
                        <td className={cx(td, "whitespace-nowrap pl-[18px] font-mono text-[13px] font-medium")}>
                          <Link href={`/returns/${r.code}${filterQuery ? `?${filterQuery}` : ""}`} aria-current={on ? "true" : undefined} className="text-ink">{r.code}</Link>
                        </td>
                        <td className={td}>{r.name}</td>
                        <td className={cx(td, "text-ink-2")}>{cadenceLabel(r.cadence)}</td>
                        <td className={cx(td, "font-mono text-[13px]")}>{draftVersion ? `${r.current_version} → ${draftVersion}` : r.current_version}</td>
                        <td className={cx(td, "whitespace-nowrap text-[13px] text-placeholder")}>[BoG due-date rule]</td>
                        <td className={cx(td, "tabular text-ink-2")}>{r.rule_count}</td>
                        <td className={cx(td, "pr-[18px]")}>
                          <Chip tone={r.status === "published" ? "green" : "amber"}>{r.status === "published" ? "Published" : "Draft pending"}</Chip>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <aside aria-label="Selected definition" className="flex min-w-0 flex-[2_1_380px] flex-col gap-5">
          {detail === null ? (
            <Card className="px-[22px] py-6">
              <p className="m-0 text-[14px] text-muted">Select a return to see its versions and any draft awaiting approval.</p>
            </Card>
          ) : !detail.ok ? (
            <Card className="px-[22px] py-6">
              <p role="alert" className="m-0 text-[14px] text-note-text">{detail.message}</p>
            </Card>
          ) : drafting ? (
            <DraftForm detail={detail.data} />
          ) : detail.data.draft ? (
            <DraftPanel detail={detail.data} canDecide={canDecide} />
          ) : (
            <VersionsPanel detail={detail.data} canDraft={canDraft} />
          )}
        </aside>
      </div>
    </>
  );
}
