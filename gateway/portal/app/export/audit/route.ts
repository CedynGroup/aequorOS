/** GET /export/audit?from=&to= — the gateway's signed audit export bundle, passed through unchanged. */

import type { NextRequest } from "next/server";
import { DATE } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { downloadFailed } from "@/lib/server/download";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const from = req.nextUrl.searchParams.get("from") ?? "";
  const to = req.nextUrl.searchParams.get("to") ?? "";
  const api = await getApi();
  try {
    const bundle = await api.exportAudit({ from: DATE.test(from) ? from : undefined, to: DATE.test(to) ? to : undefined });
    return new Response(bundle.body, {
      headers: {
        "Content-Type": bundle.content_type,
        "Content-Disposition": `attachment; filename="${bundle.filename.replace(/[^\w.-]/g, "_")}"`,
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    return downloadFailed(error, "/audit");
  }
}
