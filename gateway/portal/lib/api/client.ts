/**
 * HTTP client for the supervisor API (`/portal/api`).
 *
 * Pure and framework-free so it can be unit-tested with a stub `fetch`. The
 * server-only wrapper (`./server.ts`) supplies the base URL and the signed-in
 * user's ID token; this module never reads cookies or the environment.
 */

import { GatewayApiError, errorFromResponse } from "./errors";
import type {
  AccessOverview,
  AuditEntry,
  AuditExport,
  FileDownload,
  AuditList,
  AuditVerification,
  CalendarMonth,
  CreatedNumber,
  GatewayApi,
  InstitutionDetail,
  InstitutionList,
  Me,
  OnboardingOverview,
  Overview,
  QueryDetail,
  QueryList,
  QueryMessage,
  ReturnDefinitionDetail,
  ReturnList,
  SubmissionDetail,
  SubmissionList,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export type HttpClientOptions = {
  baseUrl: string;
  /** The BoG identity provider's ID token, sent as the Bearer. */
  token: string;
  fetch?: FetchLike;
  /** Abort a call after this many milliseconds (default 15 s). */
  timeoutMs?: number;
};

type QueryValue = string | number | undefined | null;

/** Builds `?a=1&b=2`, dropping empty values. Order follows the object. */
export function queryString(params: Record<string, QueryValue> | undefined): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Encodes one path segment (receipt ids, codes and numbers are user input). */
export function seg(value: string | number): string {
  return encodeURIComponent(String(value));
}

function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const name = match?.[1]?.trim();
  return name ? decodeURIComponent(name) : fallback;
}

export function createHttpClient(options: HttpClientOptions): GatewayApi {
  const base = options.baseUrl.replace(/\/+$/, "") + "/portal/api";
  const doFetch: FetchLike = options.fetch ?? ((input, init) => fetch(input, init));
  const timeoutMs = options.timeoutMs ?? 15_000;

  async function send(method: "GET" | "POST", path: string, body?: unknown, accept = "application/json"): Promise<Response> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${options.token}`,
      Accept: `${accept}, application/problem+json`,
    };
    const init: RequestInit = { method, headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(timeoutMs) };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await doFetch(base + path, init);
    } catch (cause) {
      const detail = cause instanceof Error && cause.name === "TimeoutError" ? "The request timed out." : undefined;
      throw new GatewayApiError({ status: 0, code: "network_error", title: "Gateway unreachable", detail });
    }
    if (!response.ok) throw await errorFromResponse(response);
    return response;
  }

  async function json<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    const response = await send(method, path, body);
    try {
      return (await response.json()) as T;
    } catch {
      throw new GatewayApiError({ status: response.status, code: "unexpected_response", title: "Unreadable response" });
    }
  }

  async function file(path: string, accept: string, fallbackName: string): Promise<FileDownload> {
    const response = await send("GET", path, undefined, accept);
    return {
      body: await response.arrayBuffer(),
      filename: filenameFrom(response.headers.get("content-disposition"), fallbackName),
      content_type: response.headers.get("content-type") ?? accept,
    };
  }

  async function empty(path: string, body?: unknown): Promise<void> {
    const response = await send("POST", path, body);
    // 201 without a documented body, or 204: drain and ignore.
    await response.arrayBuffer().catch(() => undefined);
  }

  return {
    me: () => json<Me>("GET", "/me"),
    overview: (params) => json<Overview>("GET", `/overview${queryString(params)}`),
    listSubmissions: (params) => json<SubmissionList>("GET", `/submissions${queryString(params)}`),
    getSubmission: (receiptId) => json<SubmissionDetail>("GET", `/submissions/${seg(receiptId)}`),
    acceptSubmission: (receiptId, body) => empty(`/submissions/${seg(receiptId)}/accept`, body),
    requestResubmission: (receiptId, body) =>
      json<CreatedNumber>("POST", `/submissions/${seg(receiptId)}/resubmission-request`, body),
    assignSubmission: (receiptId, body) => empty(`/submissions/${seg(receiptId)}/assign`, body),
    listQueries: (params) => json<QueryList>("GET", `/queries${queryString(params)}`),
    getQuery: (number) => json<QueryDetail>("GET", `/queries/${seg(number)}`),
    createQuery: (body) => json<CreatedNumber>("POST", "/queries", body),
    postQueryMessage: (number, body) => json<QueryMessage>("POST", `/queries/${seg(number)}/messages`, body),
    closeQuery: (number) => empty(`/queries/${seg(number)}/close`),
    listInstitutions: (params) => json<InstitutionList>("GET", `/institutions${queryString(params)}`),
    getInstitution: (code) => json<InstitutionDetail>("GET", `/institutions/${seg(code)}`),
    addInstitutionNote: (code, body) => empty(`/institutions/${seg(code)}/notes`, body),
    calendar: (params) => json<CalendarMonth>("GET", `/calendar${queryString(params)}`),
    listReturns: () => json<ReturnList>("GET", "/returns"),
    getReturn: (code) => json<ReturnDefinitionDetail>("GET", `/returns/${seg(code)}`),
    createReturnDraft: (code, body) => empty(`/returns/${seg(code)}/drafts`, body),
    approveReturnVersion: (code, version) => empty(`/returns/${seg(code)}/versions/${seg(version)}/approve`),
    returnReturnVersion: (code, version, body) => empty(`/returns/${seg(code)}/versions/${seg(version)}/return`, body),
    onboarding: () => json<OnboardingOverview>("GET", "/onboarding"),
    listAudit: (params) => json<AuditList>("GET", `/audit${queryString(params)}`),
    getAuditEntry: (seq) => json<AuditEntry>("GET", `/audit/${seg(seq)}`),
    verifyAudit: () => json<AuditVerification>("POST", "/audit/verify"),
    downloadReceipt: (receiptId) => file(`/submissions/${seg(receiptId)}/receipt`, "application/json", `${receiptId}-receipt.json`),
    downloadFiledPackage: (receiptId) => file(`/submissions/${seg(receiptId)}/package`, "application/zip", `${receiptId}-filed-package.zip`),
    downloadDocument: (receiptId, name) => file(`/submissions/${seg(receiptId)}/documents/${seg(name)}`, "*/*", name),
    async exportAudit(params): Promise<AuditExport> {
      const response = await send("GET", `/audit/export${queryString(params)}`, undefined, "application/zip");
      return {
        body: await response.arrayBuffer(),
        filename: filenameFrom(response.headers.get("content-disposition"), "audit-export.zip"),
        content_type: response.headers.get("content-type") ?? "application/zip",
      };
    },
    access: () => json<AccessOverview>("GET", "/access"),
    approveAccess: (number, body) => empty(`/access/requests/${seg(number)}/approve`, body),
    denyAccess: (number, body) => empty(`/access/requests/${seg(number)}/deny`, body),
  };
}
