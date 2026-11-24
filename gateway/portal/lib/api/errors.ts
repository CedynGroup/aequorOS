import { ERROR_CODES, type ErrorCode, type Issue, type ProblemDetails } from "./types";

/**
 * Error codes the portal can raise. The API's own codes come from SPEC
 * "Error codes"; the three extra ones describe failures that never reached a
 * problem+json body (the network, a 5xx without a body, an unreadable reply).
 */
export type ApiErrorCode = ErrorCode | "network_error" | "server_error" | "unexpected_response";

export class GatewayApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly title: string;
  readonly detail: string | undefined;
  readonly issues: Issue[];
  readonly type: string | undefined;
  readonly instance: string | undefined;

  constructor(init: {
    status: number;
    code: ApiErrorCode;
    title: string;
    detail?: string;
    issues?: Issue[];
    type?: string;
    instance?: string;
  }) {
    super(`${init.status} ${init.code}: ${init.title}`);
    this.name = "GatewayApiError";
    this.status = init.status;
    this.code = init.code;
    this.title = init.title;
    this.detail = init.detail;
    this.issues = init.issues ?? [];
    this.type = init.type;
    this.instance = init.instance;
  }
}

export function isGatewayApiError(value: unknown): value is GatewayApiError {
  return value instanceof GatewayApiError;
}

const KNOWN_CODES = new Set<string>(ERROR_CODES);

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && KNOWN_CODES.has(value);
}

/** The code implied by an HTTP status when the body names none we know. */
export function codeForStatus(status: number): ApiErrorCode {
  if (status === 400 || status === 422) return "invalid_request";
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server_error";
  return "unexpected_response";
}

function isIssue(value: unknown): value is Issue {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.rule_id === "string" &&
    (v.severity === "error" || v.severity === "warning") &&
    (v.pointer === null || typeof v.pointer === "string") &&
    typeof v.message === "string"
  );
}

/** Reads an RFC 9457 body; returns null when it is not one. */
export function parseProblem(body: unknown): Partial<ProblemDetails> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const v = body as Record<string, unknown>;
  const problem: Partial<ProblemDetails> = {};
  if (typeof v.type === "string") problem.type = v.type;
  if (typeof v.title === "string") problem.title = v.title;
  if (typeof v.status === "number") problem.status = v.status;
  if (isErrorCode(v.code)) problem.code = v.code;
  if (typeof v.detail === "string") problem.detail = v.detail;
  if (typeof v.instance === "string") problem.instance = v.instance;
  if (Array.isArray(v.issues)) problem.issues = v.issues.filter(isIssue);
  return problem;
}

/** Turns a failed HTTP response into a typed error. Never throws itself. */
export async function errorFromResponse(response: Response): Promise<GatewayApiError> {
  let body: unknown = null;
  const text = await response.text().catch(() => "");
  if (text) {
    try {
      body = JSON.parse(text) as unknown;
    } catch {
      body = null;
    }
  }
  const problem = parseProblem(body);
  const code: ApiErrorCode = problem?.code ?? codeForStatus(response.status);
  return new GatewayApiError({
    status: response.status,
    code,
    title: problem?.title ?? (response.statusText || "Request failed"),
    detail: problem?.detail,
    issues: problem?.issues,
    type: problem?.type,
    instance: problem?.instance,
  });
}

/** Plain-language messages shown to staff. Production copy: no codes. */
const MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  invalid_request: "Some of the details were not accepted. Check them and try again.",
  unauthenticated: "Your session has ended. Sign in again to continue.",
  forbidden: "This action is not open to you.",
  insufficient_scope: "Your role does not allow this action.",
  not_found: "That record could not be found. It may have been moved or closed.",
  conflict: "This record changed since you opened it. Reload the page and try again.",
  idempotency_conflict: "This request was already sent with different details.",
  rate_limited: "Too many requests in a short time. Wait a moment and try again.",
  network_error: "The gateway service could not be reached. Try again in a moment.",
  server_error: "The gateway service had a problem completing this. Try again in a moment.",
  unexpected_response: "The gateway service sent a reply the portal could not read.",
};

export function friendlyMessage(error: unknown): string {
  if (isGatewayApiError(error)) {
    const base = MESSAGES[error.code] ?? "The request could not be completed.";
    // The API's `detail` is written for the person acting; show it alongside.
    return error.detail && error.detail !== base ? `${base} ${error.detail}` : base;
  }
  return "Something went wrong. Try again in a moment.";
}
