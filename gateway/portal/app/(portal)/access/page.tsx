import type { Metadata } from "next";
import { ActionForm, SubmitButton } from "@/components/action-form";
import { Notice } from "@/components/notice";
import { Card, CardTitle, Chip, PageHeader, PageProblem, cx, field, td, th } from "@/components/ui";
import type { AccessOverview } from "@/lib/api/types";
import { formatDateTime, formatTime, isoDate } from "@/lib/format";
import { SESSION_STATUS, roleLabel } from "@/lib/labels";
import { noticeFrom } from "@/lib/notice";
import { capabilities } from "@/lib/roles";
import type { SearchParams } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";
import { load } from "@/lib/server/load";
import { decideAccess } from "../actions";

export const metadata: Metadata = { title: "Access and approvals" };

function when(iso: string): string {
  return iso.slice(0, 10) === isoDate(new Date()) ? formatTime(iso) : formatDateTime(iso, { year: false });
}

function PendingRequest({ request, canDecide }: { request: AccessOverview["pending"][number]; canDecide: boolean }) {
  const titleId = `request-${request.number}`;
  return (
    <section aria-labelledby={titleId} className="flex flex-wrap items-start justify-between gap-5 rounded-[10px] border border-note-line bg-white px-[22px] py-5">
      <div className="flex min-w-0 flex-[2_1_460px] flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <Chip tone="amber">{canDecide ? "Awaiting your decision" : "Awaiting an access approver"}</Chip>
          <span className="text-[13px] text-muted">
            Requested {when(request.requested_at)} · expires unanswered at {when(request.expires_at)}
          </span>
        </div>
        <h2 id={titleId} className="m-0 text-[19px] font-semibold">
          {request.operator_name}, {request.operator_title}, requests support access
        </h2>
        <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3 text-[14px]">
          <div className="flex flex-col gap-[3px]"><dt className="text-[12px] text-muted">Scope</dt><dd className="m-0 font-medium">{request.scope}</dd></div>
          <div className="flex flex-col gap-[3px]"><dt className="text-[12px] text-muted">Excludes</dt><dd className="m-0 font-medium">{request.excludes}</dd></div>
          <div className="flex flex-col gap-[3px]"><dt className="text-[12px] text-muted">Reason</dt><dd className="m-0 font-medium">{request.reason}</dd></div>
          <div className="flex flex-col gap-[3px]"><dt className="text-[12px] text-muted">Incident</dt><dd className={cx("m-0 font-medium", request.incident_ref && "font-mono text-[13px]")}>{request.incident_ref ?? "None given"}</dd></div>
        </dl>
      </div>
      {canDecide ? (
        <ActionForm action={decideAccess.bind(null, request.number)} label="Decision" className="flex flex-[1_1_260px] flex-col gap-2.5 rounded-lg bg-panel p-4">
          <label htmlFor={`duration-${request.number}`} className={field.label}>Approve for</label>
          <select id={`duration-${request.number}`} name="duration" defaultValue="30" className={field.select}>
            <option value="30">30 minutes</option>
            <option value="60">60 minutes</option>
            <option value="120">2 hours</option>
          </select>
          <label htmlFor={`reason-${request.number}`} className={field.label}>Reason, if you deny it</label>
          <textarea id={`reason-${request.number}`} name="reason" rows={2} maxLength={1000} className={field.textarea} />
          <div className="flex gap-2">
            <SubmitButton variant="secondary" intent="deny" className="flex-1" pendingLabel="Denying…">Deny</SubmitButton>
            <SubmitButton intent="approve" className="flex-1" pendingLabel="Approving…">Approve</SubmitButton>
          </div>
          <span className="text-[12px] leading-[1.45] text-muted">The session is recorded and streamed to the Bank’s SIEM. It ends automatically at the limit.</span>
        </ActionForm>
      ) : (
        <p className="m-0 flex-[1_1_260px] rounded-lg bg-panel p-4 text-[13px] leading-[1.5] text-muted">Only an access approver can approve or deny this request, and never their own.</p>
      )}
    </section>
  );
}

export default async function AccessPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const api = await getApi();
  const [result, me] = await Promise.all([load(api.access()), getMe()]);
  const crumbs = [{ label: "Assurance" }, { label: "Access and approvals" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Access and approvals" kind={result.kind} message={result.message} />;
  const { roles, pending, sessions } = result.data;
  const canDecide = capabilities.approveAccess(me.roles);
  const notice = noticeFrom(params, "/access");

  return (
    <>
      {notice && <Notice {...notice} />}
      <PageHeader
        crumbs={crumbs}
        title="Access and approvals"
        subtitle="Staff roles follow the Bank’s identity groups. Operator support needs the Bank’s approval every time, and has no standing access."
      />
      {pending.length === 0 ? (
        <Card className="px-[22px] py-5">
          <p className="m-0 text-[14px] text-muted">No operator is waiting for support access.</p>
        </Card>
      ) : (
        pending.map((request) => <PendingRequest key={request.number} request={request} canDecide={canDecide} />)
      )}

      <div className="flex flex-wrap items-start gap-5">
        <Card aria-labelledby="roles-title" className="flex-[3_1_620px]">
          <div className="flex flex-wrap items-baseline justify-between gap-2.5 px-[18px] pb-3 pt-[18px]">
            <CardTitle id="roles-title">Roles from the Bank’s identity groups</CardTitle>
            <span className="text-[13px] text-muted">Membership is managed in the Bank’s directory, not here</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[14px]">
              <thead>
                <tr>
                  <th scope="col" className={cx(th, "pl-[18px]")}>Identity group</th>
                  <th scope="col" className={th}>Role</th>
                  <th scope="col" className={th}>Can</th>
                  <th scope="col" className={cx(th, "pr-[18px] text-right")}>Members</th>
                </tr>
              </thead>
              <tbody>
                {roles.map((r) => (
                  <tr key={r.group}>
                    <td className={cx(td, "whitespace-nowrap pl-[18px] font-mono text-[13px] text-ink-2")}>{r.group}</td>
                    <td className={cx(td, "whitespace-nowrap font-medium")}>{roleLabel(r.role)}</td>
                    <td className={cx(td, "leading-[1.45] text-ink-2")}>{r.can}</td>
                    <td className={cx(td, "tabular pr-[18px] text-right")}>{r.members ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="flex min-w-0 flex-[2_1_380px] flex-col gap-5">
          <Card aria-labelledby="sessions-title" className="gap-2.5 px-[22px] py-5">
            <CardTitle id="sessions-title">Recent operator sessions</CardTitle>
            {sessions.length === 0 ? (
              <p className="m-0 text-[14px] text-muted">No operator sessions yet.</p>
            ) : (
              <ul className="m-0 list-none p-0">
                {sessions.map((s) => {
                  const status = SESSION_STATUS[s.status];
                  return (
                    <li key={s.number} className="flex items-center justify-between gap-3 border-t border-hairline py-2.5">
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="text-[14px] font-medium">{s.operator_name} · {s.number}</span>
                        <span className="text-[12px] text-muted">{s.detail}</span>
                      </span>
                      <Chip tone={status.tone}>{status.label}</Chip>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
          <Card aria-labelledby="sod-title" className="gap-2.5 px-[22px] py-5">
            <CardTitle id="sod-title">Separation of duties</CardTitle>
            <ul className="m-0 pl-5 text-[14px] leading-[1.6] text-ink-2">
              <li>A return definition’s drafter and approvers are three different people.</li>
              <li>Nobody can approve an operator session they requested or will use.</li>
              <li>Auditors read everything and decide nothing.</li>
              <li>Institutions and their credentials are enrolled only by the channel operator, never from this portal.</li>
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
