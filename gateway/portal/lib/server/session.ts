import "server-only";

import { cache } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { readSessionValue, type PortalSession } from "../auth/session-crypto";
import { SESSION_COOKIE, sessionKey } from "./config";

/** The signed-in session, or null. Safe to call from any server code. */
export const getSession = cache(async (): Promise<PortalSession | null> => {
  const store = await cookies();
  return readSessionValue(store.get(SESSION_COOKIE)?.value, sessionKey(), SESSION_COOKIE);
});

/** The session, or a redirect to sign-in. Every data read goes through this. */
export async function requireSession(): Promise<PortalSession> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  return session;
}
