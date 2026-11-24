/** GET /export/document/{receiptId}/{name} — one document filed with the return, unchanged. */

import { notFound } from "next/navigation";
import { RECEIPT_ID } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { downloadFailed, fileResponse } from "@/lib/server/download";

export const dynamic = "force-dynamic";

const DOCUMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,199}$/;

export async function GET(_req: Request, { params }: { params: Promise<{ receiptId: string; name: string }> }) {
  const { receiptId: rawId, name: rawName } = await params;
  const receiptId = decodeURIComponent(rawId);
  const name = decodeURIComponent(rawName);
  if (!RECEIPT_ID.test(receiptId) || !DOCUMENT_NAME.test(name)) notFound();
  const api = await getApi();
  try {
    return fileResponse(await api.downloadDocument(receiptId, name));
  } catch (error) {
    return downloadFailed(error, `/submissions/${receiptId}`);
  }
}
