"use client";

/**
 * Transport for the grant composer, for as long as the generated client has not
 * been regenerated against the Phase 4 columns.
 *
 * WHY THIS EXISTS, AND WHY IT IS NOT OPTIONAL. `BindingCreateRequestToJSON` in
 * `@aequoros/risk-service-api` serialises the seven fields it was generated
 * from and DROPS everything else. Passing `data_scope_kind` through the
 * generated operation would therefore post a grant with no coverage at all, the
 * column's server default (`all`) would apply, and an Org Owner who chose two
 * branches would have granted the whole book — silently, with a success dialog.
 * A composer that cannot send the field must not offer the choice, so the three
 * calls that carry the authority sentence are made here instead.
 *
 * Nothing about the contract is re-implemented: the bodies come from the pure
 * builders in `lib/api/grants.ts`, and the RESPONSES are parsed by the
 * generated parsers (`BindingCreateResponseFromJSON`), so dates and enums
 * arrive exactly as every other call in the app delivers them. Failures are
 * wrapped in the generated `ResponseError` and handed to `normalizeApiError`,
 * which is what gives the review step its SoD findings — identical error
 * behaviour to the generated client, not a second error language.
 *
 * Auth mirrors `client.ts`'s `Configuration.accessToken`, including the
 * staff-inspection branch, so an inspection hand-off cannot accidentally
 * authenticate as the tenant (or vice versa). Same precedent as
 * `lib/api/reportComparison.ts` and `lib/api/marketDataSources.ts`.
 *
 * AFTER `mise run risk-service:openapi-client` regenerates the package against
 * P4-A's schema, delete this module's three grant calls and go back through
 * `authorizationApi` / `authApi`. The branch-directory call becomes a generated
 * operation at the same time.
 */

import { useQuery } from "@tanstack/react-query";
import { getSession } from "next-auth/react";
import {
  BindingCreateResponseFromJSON,
  BindingPreviewReadFromJSON,
  ResponseError,
  type BindingCreateResponse,
  type BindingPreviewRead,
} from "@aequoros/risk-service-api";
import { ApiError, apiBaseUrl } from "@/lib/api/client";
import {
  getImpersonationBearer,
  impersonationMarkerPresent,
  markImpersonationExpired,
} from "@/lib/api/impersonation";
import { getAccessToken, setAccessToken } from "@/lib/api/token";
import {
  grantCreateBody,
  grantPreviewBody,
  institutionBranchesKey,
  parseBranchDirectory,
  ssoApprovalBody,
  type BranchDirectory,
  type GrantDraft,
} from "@/lib/api/grants";
import { useQueryAuthorityScope } from "@/lib/api/useQueryScope";

async function bearerToken(): Promise<string> {
  if (impersonationMarkerPresent()) {
    const inspecting = await getImpersonationBearer();
    if (inspecting) return inspecting;
  }
  const cached = getAccessToken();
  if (cached) return cached;
  const session = await getSession();
  const token = session?.accessToken ?? "";
  if (token) setAccessToken(token);
  return token;
}

async function request(
  path: string,
  init?: Readonly<{ method: string; body: unknown }>,
): Promise<unknown> {
  const token = await bearerToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: init?.method ?? "GET",
      headers,
      body: init ? JSON.stringify(init.body) : undefined,
    });
  } catch (error) {
    throw new ApiError({
      message:
        "Could not reach the risk service. Check that the backend is running.",
      status: null,
      code: "network_error",
      errorCode: null,
      details: error instanceof Error ? error.message : error,
    });
  }
  if (!response.ok) {
    if (impersonationMarkerPresent() && response.status === 401) {
      markImpersonationExpired();
    }
    throw new ResponseError(response, "Response returned an error code");
  }
  return (await response.json()) as unknown;
}

/** `GET /organization/institutions/{institution_id}/branches`. */
export async function fetchInstitutionBranches(
  institutionId: string,
): Promise<BranchDirectory> {
  const body = await request(
    `/organization/institutions/${encodeURIComponent(institutionId)}/branches`,
  );
  return parseBranchDirectory(body, institutionId);
}

export async function previewScopedGrant(
  draft: GrantDraft,
  principalUserId: string,
): Promise<BindingPreviewRead> {
  const body = await request("/authorization/bindings/preview", {
    method: "POST",
    body: grantPreviewBody(draft, principalUserId),
  });
  return BindingPreviewReadFromJSON(body);
}

export async function createScopedGrant(
  draft: GrantDraft,
  principalUserId: string,
  expectedAuthoritySentence: string,
): Promise<BindingCreateResponse> {
  const body = await request("/authorization/bindings", {
    method: "POST",
    body: grantCreateBody(draft, principalUserId, expectedAuthoritySentence),
  });
  return BindingCreateResponseFromJSON(body);
}

export async function approveSsoAccessWithScopedGrant(
  userId: string,
  draft: GrantDraft,
  expectedAuthoritySentence: string,
): Promise<BindingCreateResponse> {
  const body = await request(
    `/auth/sso/access-requests/${encodeURIComponent(userId)}/approve`,
    { method: "POST", body: ssoApprovalBody(draft, expectedAuthoritySentence) },
  );
  return BindingCreateResponseFromJSON(body);
}

/**
 * The branches of one institution, for the composer's coverage control.
 *
 * `retry: false` on purpose: a failure has to be VISIBLE promptly, because the
 * only honest thing the control can do without this list is decline to offer
 * branch coverage and say why. A silent retry loop would leave it looking
 * merely slow.
 */
export function useInstitutionBranches(institutionId: string | null) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: institutionBranchesKey(scope, institutionId),
    queryFn: () => fetchInstitutionBranches(institutionId!),
    enabled: Boolean(institutionId),
    retry: false,
  });
}
