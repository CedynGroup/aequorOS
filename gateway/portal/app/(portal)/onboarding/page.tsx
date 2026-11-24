import type { Metadata } from "next";
import Link from "next/link";
import { Card, Chip, PageHeader, PageProblem, Row, cx, td, th } from "@/components/ui";
import type { OnboardingRegisterRow } from "@/lib/api/types";
import { formatDate, formatNumber, plural } from "@/lib/format";
import { REGISTER_COUNTS, channelLabel, institutionTypeLabel, registerCountKey, registerStatus, softwareLabel } from "@/lib/labels";
import { INSTITUTION_CODE, param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load, optional } from "@/lib/server/load";

export const metadata: Metadata = { title: "Institutions on the channel" };

function certificateText(row: OnboardingRegisterRow): string {
  if (row.certificate_not_after) return formatDate(row.certificate_not_after);
  return row.client_id ? "Not recorded" : "—";
}

/**
 * Read-only. Institutions, their API clients and signing keys are enrolled
 * and suspended by the channel operator through its own API (SPEC Part 5);
 * BoG staff only see the result here.
 */
export default async function ChannelRegisterPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const requested = param(params, "institution");
  const selectedCode = requested && INSTITUTION_CODE.test(requested) ? requested : null;
  const api = await getApi();
  const [result, profile] = await Promise.all([load(api.onboarding()), selectedCode ? optional(api.getInstitution(selectedCode)) : Promise.resolve(null)]);
  const crumbs = [{ label: "Configuration" }, { label: "Channel register" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Institutions on the channel" kind={result.kind} message={result.message} />;
  const { stages, register } = result.data;

  const counts = new Map<string, number>(REGISTER_COUNTS.map((label) => [label, 0]));
  for (const s of stages) counts.set(registerCountKey(s.stage), (counts.get(registerCountKey(s.stage)) ?? 0) + s.count);
  const total = stages.reduce((sum, s) => sum + s.count, 0) || register.length;
  const expiring = register.filter((r) => r.certificate_expiring).length;
  const selected = selectedCode ? register.find((r) => r.code === selectedCode) ?? null : null;

  return (
    <>
      <PageHeader crumbs={crumbs} title="Institutions on the channel" subtitle="Which institutions file through the gateway, how each one authenticates, and when its certificate expires" />

      <ul aria-label="Institutions by status" className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3 p-0">
        {REGISTER_COUNTS.map((label) => (
          <li key={label} className="flex flex-col gap-1 rounded-[10px] border border-line bg-white px-4 py-3.5">
            <span className="text-[15px] font-semibold">{label}</span>
            <span className="tabular text-[24px] font-semibold text-action">{formatNumber(counts.get(label) ?? 0)}</span>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-start gap-5">
        <Card aria-labelledby="register-title" className="flex-[3_1_640px]">
          <div className="flex flex-wrap items-baseline justify-between gap-2.5 px-[18px] pb-3 pt-[18px]">
            <h2 id="register-title" className="m-0 text-[17px] font-semibold">Institution register</h2>
            <span className="text-[13px] text-muted">
              {plural(total, "institution")} · {expiring === 1 ? "1 certificate expires" : `${expiring} certificates expire`} within 30 days
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse text-[14px]">
              <thead>
                <tr>
                  <th scope="col" className={cx(th, "pl-[18px]")}>Institution</th>
                  <th scope="col" className={th}>Files by</th>
                  <th scope="col" className={th}>API client</th>
                  <th scope="col" className={th}>Certificate</th>
                  <th scope="col" className={th}>Portal users</th>
                  <th scope="col" className={cx(th, "pr-[18px]")}>Status</th>
                </tr>
              </thead>
              <tbody>
                {register.map((r) => {
                  const on = r.code === selectedCode;
                  const status = registerStatus(r.status);
                  return (
                    <tr key={r.code} className={on ? "bg-selected" : "bg-white"}>
                      <td className={cx(td, "pl-[18px]")}>
                        <div className="flex flex-col gap-0.5">
                          <Link href={`/onboarding?institution=${r.code}`} aria-current={on ? "true" : undefined} className="font-medium text-ink">
                            {r.name}
                          </Link>
                          <span className="text-[12px] text-muted">{institutionTypeLabel(r.type)}</span>
                        </div>
                      </td>
                      <td className={cx(td, "whitespace-nowrap text-ink-2")}>
                        <div className="flex flex-col gap-0.5">
                          <span>{channelLabel(r.channel)}</span>
                          <span className="text-[12px] text-muted">{softwareLabel(r.software_declared, r.channel)}</span>
                        </div>
                      </td>
                      <td className={cx(td, "font-mono text-[13px] text-ink-2")}>{r.client_id ?? "—"}</td>
                      <td className={cx(td, "whitespace-nowrap", r.certificate_expiring ? "font-semibold text-warn" : "text-ink-2")}>
                        <div className="flex flex-col gap-0.5">
                          <span>{certificateText(r)}</span>
                          {r.certificate_expiring && <span className="text-[12px] font-medium">Expires within 30 days</span>}
                        </div>
                      </td>
                      <td className={cx(td, "text-ink-2")}>{r.portal_users}</td>
                      <td className={cx(td, "pr-[18px]")}><Chip tone={status.tone}>{status.label}</Chip></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        <aside aria-labelledby="detail-title" className="flex min-w-0 flex-[2_1_360px] flex-col gap-3.5 rounded-[10px] border border-line bg-white px-[22px] py-5">
          {selected ? (
            <>
              <div className="flex flex-col gap-1.5">
                <span className="text-[13px] text-muted">Institution code {selected.code}</span>
                <h2 id="detail-title" className="m-0 text-[19px] font-semibold">{selected.name}</h2>
                <span className="text-[13px] text-muted">{institutionTypeLabel(selected.type)} · {channelLabel(selected.channel)}</span>
              </div>
              <dl className="m-0 flex flex-col gap-2.5 text-[14px]">
                <Row label="Enrolled on">{formatDate(selected.enrolled_at)}</Row>
                <Row label="Status">
                  <Chip tone={registerStatus(selected.status).tone}>{registerStatus(selected.status).label}</Chip>
                </Row>
                <Row label="API client">{selected.client_id ? <span className="font-mono text-[13px]">{selected.client_id}</span> : "None issued"}</Row>
                <Row label="Certificate expiry">
                  <span className={selected.certificate_expiring ? "font-semibold text-warn" : undefined}>
                    {certificateText(selected)}
                    {selected.certificate_expiring && " · within 30 days"}
                  </span>
                </Row>
                <Row label="Signing keys">
                  {profile === null ? (
                    "Not available"
                  ) : profile.credentials.signing_keys.length === 0 ? (
                    "None registered"
                  ) : (
                    <ul className="m-0 flex list-none flex-col items-end gap-1 p-0">
                      {profile.credentials.signing_keys.map((k) => (
                        <li key={k.kid}>
                          <span className="font-mono text-[13px]">{k.kid}</span> <span className="text-muted">({k.status})</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Row>
              </dl>
              <Link href={`/institutions/${selected.code}`} className="text-[13px] font-medium">Open the supervision profile</Link>
            </>
          ) : (
            <>
              <h2 id="detail-title" className="m-0 text-[17px] font-semibold">Institution detail</h2>
              <p className="m-0 text-[14px] text-muted">Select an institution in the register to see its status, keys and certificate.</p>
            </>
          )}
          <p className="m-0 border-t border-hairline pt-3 text-[13px] leading-[1.5] text-muted">Institutions are enrolled and suspended by the channel operator.</p>
        </aside>
      </div>
    </>
  );
}
