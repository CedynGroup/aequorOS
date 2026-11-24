/** GET /export/package/{receiptId} — the filing as received (zip), passed through unchanged. */

import { notFound } from "next/navigation";
import { RECEIPT_ID } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { downloadFailed, fileResponse } from "@/lib/server/download";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: Promise<{ receiptId: string }> }) {
  const receiptId = decodeURIComponent((await params).receiptId);
  if (!RECEIPT_ID.test(receiptId)) notFound();
  const api = await getApi();
  try {
    return fileResponse(await api.downloadFiledPackage(receiptId));
  } catch (error) {
    return downloadFailed(error, `/submissions/${receiptId}`);
  }
}
