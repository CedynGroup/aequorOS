/** GET /export/cycle?reporting_date= — the overview's compliance grid as CSV. */

import type { NextRequest } from "next/server";
import { toCsv } from "@/lib/csv";
import { HEAT_STATE } from "@/lib/labels";
import { DATE } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { csvResponse, downloadFailed } from "@/lib/server/download";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const requested = req.nextUrl.searchParams.get("reporting_date") ?? "";
  const reportingDate = DATE.test(requested) ? requested : undefined;
  const api = await getApi();
  try {
    const o = await api.overview(reportingDate ? { reporting_date: reportingDate } : {});
    const rows: (string | number)[][] = [
      ["Reporting date", o.reporting_date],
      ["Due", o.totals.due, "Received", o.totals.received, "Accepted", o.totals.accepted, "Outstanding past due", o.totals.outstanding_past_due, "Filed late", o.totals.filed_late],
      [],
      ["Institution", "Institution code", ...o.heatmap.return_codes, "Received", "Due"],
      ...o.heatmap.rows.map((r) => [
        r.institution_name,
        r.institution_code,
        ...o.heatmap.return_codes.map((code) => {
          const cell = r.cells.find((c) => c.return_code === code);
          return cell ? `${HEAT_STATE[cell.state].label}${cell.receipt_id ? ` (${cell.receipt_id})` : ""}` : "";
        }),
        r.received,
        r.due,
      ]),
    ];
    return csvResponse(toCsv(rows), `cycle-report-${o.reporting_date}.csv`);
  } catch (error) {
    return downloadFailed(error, "/");
  }
}
