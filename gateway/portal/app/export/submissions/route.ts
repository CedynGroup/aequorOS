/**
 * GET /export/submissions — the inbox as CSV: either the filings selected on
 * the page (`?receipt=…` repeated) or every filing matching the filters.
 */

import type { NextRequest } from "next/server";
import type { SubmissionRow } from "@/lib/api/types";
import { toCsv } from "@/lib/csv";
import { channelLabel, submissionStatus } from "@/lib/labels";
import { RECEIPT_ID } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { csvResponse, downloadFailed } from "@/lib/server/download";

export const dynamic = "force-dynamic";

const MAX_ROWS = 5000;
const FILTERS = ["status", "return_code", "institution_code", "reporting_date", "channel", "q"] as const;

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams;
  const selected = search.getAll("receipt").filter((r) => RECEIPT_ID.test(r)).slice(0, 200);
  const api = await getApi();
  try {
    let rows: SubmissionRow[] = [];
    if (selected.length > 0) {
      rows = (await Promise.all(selected.map((id) => api.getSubmission(id)))).map((d) => d.row);
    } else {
      const filters = Object.fromEntries(FILTERS.map((k) => [k, search.get(k) || undefined]));
      let cursor: string | undefined;
      do {
        const page = await api.listSubmissions({ ...filters, cursor, limit: 200 });
        rows.push(...page.items);
        cursor = page.next_cursor ?? undefined;
      } while (cursor && rows.length < MAX_ROWS);
    }
    const csv = toCsv([
      ["Receipt", "Received (GMT)", "Institution", "Institution code", "Return", "Return name", "Reporting date", "Version", "Channel", "Software declared", "Rules passed", "Rules total", "Status", "Reviewer"],
      ...rows.map((r) => [
        r.receipt_id,
        r.received_at,
        r.institution_name,
        r.institution_code,
        r.return_code,
        r.return_name,
        r.reporting_date,
        r.version,
        channelLabel(r.channel),
        r.software_declared,
        r.validation?.passed ?? null,
        r.validation?.total ?? null,
        submissionStatus(r.status).label,
        r.reviewer_name,
      ]),
    ]);
    return csvResponse(csv, `submissions-${new Date().toISOString().slice(0, 10)}.csv`);
  } catch (error) {
    return downloadFailed(error, "/submissions");
  }
}
