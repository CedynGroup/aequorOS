import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";
import { createHttpClient } from "../api/client";
import { isGatewayApiError } from "../api/errors";
import { createMockGateway } from "../api/mock/store";
import type { GatewayApi, Me } from "../api/types";
import { rolesForGroups } from "../auth/staff";
import type { PortalSession } from "../auth/session-crypto";
import { portalConfig } from "./config";
import { requireSession } from "./session";

/** A session that ended at the API (401) is cleared and sent back to sign-in. */
function withSessionEnd(api: GatewayApi): GatewayApi {
  const wrapped = {} as Record<string, unknown>;
  for (const [name, fn] of Object.entries(api) as [string, (...args: unknown[]) => Promise<unknown>][]) {
    wrapped[name] = async (...args: unknown[]) => {
      try {
        return await fn(...args);
      } catch (error) {
        if (isGatewayApiError(error) && error.code === "unauthenticated") redirect("/auth/expired");
        throw error;
      }
    };
  }
  return wrapped as unknown as GatewayApi;
}

export function mockViewer(session: PortalSession): Me {
  return { subject: session.subject, name: session.name, email: session.email, roles: rolesForGroups(session.groups ?? []) };
}

/** The gateway API as the signed-in user: the real service, or mock mode's in-memory one. */
export const getApi = cache(async (): Promise<GatewayApi> => {
  const session = await requireSession();
  const config = portalConfig();
  if (config.mock) {
    if (session.mode !== "mock") redirect("/auth/expired");
    return withSessionEnd(createMockGateway(mockViewer(session)));
  }
  if (session.mode !== "oidc" || !session.id_token) redirect("/auth/expired");
  return withSessionEnd(createHttpClient({ baseUrl: config.apiUrl, token: session.id_token }));
});

/** `GET /portal/api/me`, once per request. */
export const getMe = cache(async (): Promise<Me> => (await getApi()).me());
