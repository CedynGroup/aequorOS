/**
 * Portal configuration, read from the environment (server-side only; nothing
 * here is NEXT_PUBLIC). `readConfig` is pure so it can be tested with any
 * environment record.
 */

export type GatewayEnv = "local" | "test" | "sandbox" | "production";

export type PortalConfig = {
  env: GatewayEnv;
  mock: boolean;
  apiUrl: string;
  oidc: {
    issuer: string;
    clientId: string;
    clientSecret: string | null;
    scope: string;
  };
  /** Explicit public origin for redirect URIs when behind a proxy. */
  baseUrl: string | null;
  sessionSecret: string | null;
};

type Env = Readonly<Record<string, string | undefined>>;

const TRUE = new Set(["1", "true", "yes", "on"]);
const ENVS = new Set<GatewayEnv>(["local", "test", "sandbox", "production"]);

function trimmed(env: Env, key: string): string | undefined {
  const value = env[key]?.trim();
  return value ? value : undefined;
}

/**
 * `GATEWAY_ENV` when set; otherwise `local` under `next dev`/tests and
 * `production` for anything else, so a deployment that forgets to set it gets
 * the strict behaviour rather than development conveniences.
 */
export function gatewayEnv(env: Env): GatewayEnv {
  const raw = trimmed(env, "GATEWAY_ENV")?.toLowerCase();
  if (raw) {
    if (!ENVS.has(raw as GatewayEnv)) throw new Error(`GATEWAY_ENV must be one of local, test, sandbox, production (got "${raw}").`);
    return raw as GatewayEnv;
  }
  return env.NODE_ENV === "development" || env.NODE_ENV === "test" ? "local" : "production";
}

export function isDevelopmentEnv(value: GatewayEnv): boolean {
  return value === "local" || value === "test";
}

function requireHttps(name: string, value: string, env: GatewayEnv): string {
  const url = new URL(value);
  if (env === "production" && url.protocol !== "https:") {
    throw new Error(`${name} must use https in production.`);
  }
  return value.replace(/\/+$/, "");
}

export function readConfig(env: Env): PortalConfig {
  const gw = gatewayEnv(env);
  const mock = TRUE.has(trimmed(env, "GATEWAY_PORTAL_MOCK")?.toLowerCase() ?? "");
  if (mock && gw === "production") {
    throw new Error("GATEWAY_PORTAL_MOCK is refused when GATEWAY_ENV is production.");
  }
  const devDefaults = isDevelopmentEnv(gw);
  const issuer = trimmed(env, "GATEWAY_OIDC_ISSUER") ?? (devDefaults ? "http://localhost:8200/idp" : undefined);
  const clientId = trimmed(env, "GATEWAY_OIDC_CLIENT_ID") ?? (devDefaults ? "srg-portal" : undefined);
  const apiUrl = trimmed(env, "GATEWAY_API_URL") ?? (devDefaults ? "http://localhost:8200" : undefined);
  if (!mock && (!issuer || !clientId || !apiUrl)) {
    throw new Error("GATEWAY_API_URL, GATEWAY_OIDC_ISSUER and GATEWAY_OIDC_CLIENT_ID must be set outside local and test.");
  }
  return {
    env: gw,
    mock,
    apiUrl: requireHttps("GATEWAY_API_URL", apiUrl ?? "http://localhost:8200", gw),
    oidc: {
      issuer: requireHttps("GATEWAY_OIDC_ISSUER", issuer ?? "http://localhost:8200/idp", gw),
      clientId: clientId ?? "srg-portal",
      clientSecret: trimmed(env, "GATEWAY_OIDC_CLIENT_SECRET") ?? null,
      scope: trimmed(env, "GATEWAY_OIDC_SCOPE") ?? "openid profile email",
    },
    baseUrl: trimmed(env, "GATEWAY_PORTAL_BASE_URL")?.replace(/\/+$/, "") ?? null,
    sessionSecret: trimmed(env, "GATEWAY_PORTAL_SESSION_SECRET") ?? null,
  };
}
