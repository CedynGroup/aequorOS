import "server-only";

import { deriveKey } from "../auth/session-crypto";
import { randomUrlSafe } from "../auth/pkce";
import { isDevelopmentEnv, readConfig, type PortalConfig } from "../config";

let cached: PortalConfig | null = null;

export function portalConfig(): PortalConfig {
  cached ??= readConfig(process.env);
  return cached;
}

const EPHEMERAL = Symbol.for("srg.portal.ephemeralSessionSecret");
let key: Buffer | null = null;

/**
 * The cookie-encryption key. Outside local/test the secret must be set; in
 * local/test a missing secret is replaced by a random one held for the life
 * of the process (sessions end when the server restarts).
 */
export function sessionKey(): Buffer {
  if (key) return key;
  const config = portalConfig();
  if (config.sessionSecret) {
    key = deriveKey(config.sessionSecret);
    return key;
  }
  if (!isDevelopmentEnv(config.env)) {
    throw new Error("GATEWAY_PORTAL_SESSION_SECRET must be set (at least 32 characters).");
  }
  const holder = globalThis as unknown as Record<symbol, string | undefined>;
  if (!holder[EPHEMERAL]) {
    holder[EPHEMERAL] = randomUrlSafe(48);
    console.warn("[portal] GATEWAY_PORTAL_SESSION_SECRET is not set; using a random secret until this server restarts.");
  }
  key = deriveKey(holder[EPHEMERAL]);
  return key;
}

/** Cookies are Secure whenever the portal is served over https. */
export function secureCookies(): boolean {
  const config = portalConfig();
  return config.env === "production" || Boolean(config.baseUrl?.startsWith("https://"));
}

export const SESSION_COOKIE = "srg_session";
export const OAUTH_COOKIE = "srg_oauth";
