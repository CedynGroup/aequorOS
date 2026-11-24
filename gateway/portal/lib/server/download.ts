import "server-only";

import { friendlyMessage, isGatewayApiError } from "../api/errors";
import type { FileDownload } from "../api/types";

export function csvResponse(body: string, filename: string): Response {
  return new Response(body, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename.replace(/[^\w.-]/g, "_")}"`,
      "Cache-Control": "no-store",
    },
  });
}

/** A file from the gateway, passed through unchanged as an attachment. */
export function fileResponse(file: FileDownload): Response {
  return new Response(file.body, {
    headers: {
      "Content-Type": file.content_type,
      "Content-Disposition": `attachment; filename="${file.filename.replace(/[^\w.-]/g, "_")}"`,
      "Cache-Control": "no-store",
    },
  });
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** A download that failed: a small page that says why, with a way back. */
export function downloadFailed(error: unknown, back: string): Response {
  if (!isGatewayApiError(error)) throw error;
  const status = error.status >= 400 ? error.status : 502;
  const message = escapeHtml(friendlyMessage(error));
  return new Response(
    `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Download not available</title></head><body style="font-family:Inter,system-ui,sans-serif;background:#F4F6F8;color:#0E1726;margin:0;padding:48px 16px"><main style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #DDE2E8;border-radius:10px;padding:24px"><h1 style="margin:0 0 8px;font-size:20px">The download could not be prepared</h1><p style="margin:0 0 16px;font-size:14px;line-height:1.5">${message}</p><a href="${escapeHtml(back)}" style="color:#1F4FD1;font-size:14px;font-weight:600">Go back</a></main></body></html>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
  );
}
