import type { Metadata } from "next";
import Link from "next/link";
import { Card, Chip, PageHeader, PageProblem, button, cx, field, td, th } from "@/components/ui";
import { formatPercent } from "@/lib/format";
import { channelLabel, institutionTypeLabel, registerStatus, softwareLabel } from "@/lib/labels";
import { param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load } from "@/lib/server/load";

export const metadata: Metadata = { title: "Institutions" };

export default async function InstitutionsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const q = param(params, "q");
  const api = await getApi();
  const result = await load(api.listInstitutions(q ? { q } : {}));
  const crumbs = [{ label: "Supervision" }, { label: "Institutions" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Institutions" kind={result.kind} message={result.message} />;
  const items = [...result.data.items].sort((a, b) => b.outstanding - a.outstanding || a.name.localeCompare(b.name));

  return (
    <>
      <PageHeader crumbs={crumbs} title="Institutions" subtitle="Every institution that files with the Bank, its filing record and what is outstanding" />
      <form role="search" action="/institutions" method="get" className="flex flex-wrap items-end gap-3 rounded-[10px] border border-line bg-white px-[18px] py-4">
        <div className="flex flex-[2_1_260px] flex-col gap-1.5">
          <label htmlFor="inst-search" className={field.label}>Search</label>
          <input id="inst-search" name="q" type="search" defaultValue={q} placeholder="Institution name or code" className={field.input} />
        </div>
        <div className="flex gap-2">
          <button type="submit" className={button.secondary}>Search</button>
          {q && <Link href="/institutions" className={button.quiet}>Clear</Link>}
        </div>
      </form>
      <Card aria-label="Institutions">
        {items.length === 0 ? (
          <p className="m-0 px-[18px] py-8 text-[14px] text-muted">No institution matches “{q}”.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse text-[14px]">
              <thead>
                <tr>
                  <th scope="col" className={cx(th, "pl-[18px]")}>Institution</th>
                  <th scope="col" className={th}>Code</th>
                  <th scope="col" className={th}>Files by</th>
                  <th scope="col" className={cx(th, "text-right")}>On time, 12 months</th>
                  <th scope="col" className={cx(th, "text-right")}>Outstanding</th>
                  <th scope="col" className={cx(th, "pr-[18px]")}>Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => {
                  const status = registerStatus(i.status);
                  return (
                    <tr key={i.code}>
                      <td className={cx(td, "pl-[18px]")}>
                        <div className="flex flex-col gap-0.5">
                          <Link href={`/institutions/${i.code}`} className="font-medium">{i.name}</Link>
                          <span className="text-[12px] text-muted">{institutionTypeLabel(i.type)}</span>
                        </div>
                      </td>
                      <td className={cx(td, "font-mono text-[13px] text-ink-2")}>{i.code}</td>
                      <td className={cx(td, "whitespace-nowrap text-ink-2")}>
                        <div className="flex flex-col gap-0.5">
                          <span>{channelLabel(i.channel)}</span>
                          <span className="text-[12px] text-muted">{softwareLabel(i.software_declared, i.channel)}</span>
                        </div>
                      </td>
                      <td className={cx(td, "tabular text-right")}>{formatPercent(i.on_time_12m)}</td>
                      <td className={cx(td, "tabular text-right", i.outstanding > 0 && "font-semibold text-bad")}>{i.outstanding}</td>
                      <td className={cx(td, "pr-[18px]")}><Chip tone={status.tone}>{status.label}</Chip></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
