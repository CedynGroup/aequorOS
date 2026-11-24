/** GET /export/institution/{code} — an institution's filing record and obligations as CSV. */

import type { NextRequest } from "next/server";
import { toCsv } from "@/lib/csv";
import { HISTORY_STATE, OBLIGATION_STATUS } from "@/lib/labels";
import { INSTITUTION_CODE } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { csvResponse, downloadFailed } from "@/lib/server/download";

export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, ctx: RouteContext<"/export/institution/[code]">) {
  const { code } = await ctx.params;
  if (!INSTITUTION_CODE.test(code)) return new Response("Not found", { status: 404 });
  const api = await getApi();
  try {
    const d = await api.getInstitution(code);
    const csv = toCsv([
      ["Institution", d.name, "Institution code", d.code],
      [],
      ["Return", "Reporting date", "State", "Receipt"],
      ...d.history.rows.flatMap((row) => row.cells.map((c) => [row.return_code, c.reporting_date, HISTORY_STATE[c.state].label, c.receipt_id])),
      [],
      ["Obligation", "Reporting date", "Due (GMT)", "Status", "Receipt"],
      ...d.obligations.map((o) => [o.return_code, o.reporting_date, o.due_at, OBLIGATION_STATUS[o.status].label, o.receipt_id]),
    ]);
    return csvResponse(csv, `filing-record-${d.code}.csv`);
  } catch (error) {
    return downloadFailed(error, `/institutions/${code}`);
  }
}
