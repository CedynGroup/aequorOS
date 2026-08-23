/**
 * NextAuth (Auth.js v5) — the dashboard's authentication broker.
 *
 * Two ways in, both ending in an AequorOS *app* token that the backend issues and
 * verifies (zero-trust): a Credentials provider (email + password → backend
 * `/auth/login`) and AequorOS' own OIDC SSO — the bank's IdP (Google Workspace,
 * Entra, Okta, …) is configured per-org in the backend (`sso_connections`,
 * Settings → Authentication), and NextAuth loads that client config lazily via an
 * internal server-to-server endpoint gated by SSO_INTERNAL_KEY. No third-party
 * auth broker. The backend independently re-verifies every id_token via
 * `/auth/sso`, then its access/refresh tokens live in the NextAuth session; the
 * API client attaches the access token as `Authorization: Bearer` on every call.
 * The browser never sets the tenant identity — it comes from the verified token.
 */
import NextAuth, { customFetch, type NextAuthConfig } from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { OutboundTargetBlocked, checkOutboundUrl, guardedFetchFor } from './lib/outbound';

const apiOrigin = (process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? 'http://localhost:8000')
  .replace(/\/api\/v1\/?$/, '');

/** Decode a JWT payload (no verification — the token was just issued by our backend). */
function decodeJwt(token: string): Record<string, unknown> {
  const payload = token.split('.')[1];
  const json = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString();
  return JSON.parse(json) as Record<string, unknown>;
}

/**
 * Marks a sign-in failure as "the service could not answer", not "these
 * credentials are wrong".
 *
 * On 2026-07-26 the production API crash-looped and every attempt to sign in
 * read "Invalid email or password" — the operator was told their password was
 * wrong while the backend was not running at all. A 502 from the proxy, a
 * refused connection and a genuine 401 all collapsed into one `null` here.
 */
export class AuthServiceUnavailable extends Error {
  constructor(detail: string) {
    super(`service_unavailable: ${detail}`);
    this.name = 'AuthServiceUnavailable';
  }
}

async function backendTokens(path: string, body: unknown) {
  let res: Response;
  try {
    res = await fetch(`${apiOrigin}/api/v1/auth/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    // DNS failure, refused connection, TLS error, timeout — nothing to do with
    // what the user typed.
    throw new AuthServiceUnavailable(cause instanceof Error ? cause.message : 'unreachable');
  }
  // 5xx is the service failing; only a 4xx is a statement about the credentials.
  if (res.status >= 500) {
    throw new AuthServiceUnavailable(`status ${res.status}`);
  }
  if (!res.ok) return null;
  return (await res.json()) as { access_token: string; refresh_token: string };
}

/** Epoch ms at which a freshly issued access token expires (from its `exp` claim). */
function accessTokenExpiryMs(accessToken: string): number {
  const exp = decodeJwt(accessToken).exp;
  return typeof exp === 'number' ? exp * 1000 : 0;
}

/**
 * Exchange the stored refresh token for a fresh access token (rotation). The
 * backend access token is short-lived (~15 min) while the NextAuth session lives
 * far longer, so without this every dashboard call 401s once the token expires.
 * On failure (refresh expired/revoked) the caller flags the token so the UI
 * re-authenticates rather than looping on 401s.
 */
async function refreshAccessToken(token: import('next-auth/jwt').JWT) {
  if (!token.refreshToken) return { ...token, error: 'RefreshTokenError' as const };
  const tokens = await backendTokens('refresh', { refresh_token: token.refreshToken });
  if (!tokens) return { ...token, error: 'RefreshTokenError' as const };
  // Re-read identity claims from the fresh token so name/role/email stay current
  // (and pre-existing sessions pick up newly-added claims like `name`).
  const claims = decodeJwt(tokens.access_token);
  return {
    ...token,
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    accessTokenExpires: accessTokenExpiryMs(tokens.access_token),
    name: claims.name ? String(claims.name) : token.name,
    email: claims.email ? String(claims.email) : token.email,
    roles: (claims.roles as string[]) ?? token.roles,
    organizationId: claims.org ? String(claims.org) : token.organizationId,
    error: undefined,
  };
}

// Refresh a little before the token actually expires so an in-flight request never
// races the boundary.
const REFRESH_SKEW_MS = 60_000;

// --- SSO client config (from the backend, where the org admin manages it) ------
interface SsoClientConfig {
  enabled: boolean;
  issuer?: string | null;
  client_id?: string | null;
  client_secret?: string | null;
}

// Module-level cache: at most one backend round-trip per minute per server
// instance, and misses (backend down, SSO disabled) are cached too so a broken
// backend can't add latency to every auth route.
let ssoCache: { config: SsoClientConfig | null; fetchedAt: number } = {
  config: null,
  fetchedAt: 0,
};
const SSO_CACHE_MS = 60_000;

/**
 * Refuse an SSO config whose issuer is not a routable public destination.
 *
 * `issuer` is set by an org admin (Settings → Authentication) and openid-client
 * fetches it from INSIDE this Node process — its discovery request and its
 * token-endpoint exchange are server-side fetches the backend's Python egress
 * guard structurally cannot see, because they happen in a different runtime in
 * a different container. Without this, `http://169.254.169.254/…` as an issuer
 * aims the dashboard server at cloud instance metadata.
 *
 * A blocked issuer is reported as "no SSO", not as an error: the provider is
 * simply never constructed, so there is nothing to sign in with and nothing
 * about the deployment's network leaks into a page. The reason and the resolved
 * address go to the server log only.
 */
export async function vetSsoIssuer(
  config: SsoClientConfig | null,
  options?: Parameters<typeof checkOutboundUrl>[1],
): Promise<SsoClientConfig | null> {
  if (!config?.enabled || !config.issuer) return config;
  try {
    await checkOutboundUrl(config.issuer, { field: 'SSO issuer', ...options });
    return config;
  } catch (error) {
    if (error instanceof OutboundTargetBlocked) {
      console.warn(
        `[sso] issuer blocked by the egress guard (${error.reason}): ${error.internalDetail}`,
      );
      return null;
    }
    throw error;
  }
}

async function fetchSsoConfig(): Promise<SsoClientConfig | null> {
  const internalKey = process.env.SSO_INTERNAL_KEY;
  if (!internalKey) return null;
  if (Date.now() - ssoCache.fetchedAt < SSO_CACHE_MS) return ssoCache.config;
  try {
    const res = await fetch(`${apiOrigin}/api/v1/auth/sso/client-config`, {
      headers: { 'X-Internal-Auth': internalKey },
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    // Vetted BEFORE it is cached, so the cache can only ever hold a permitted
    // issuer and the provider below is built from a checked value by construction.
    ssoCache = {
      config: await vetSsoIssuer(res.ok ? ((await res.json()) as SsoClientConfig) : null),
      fetchedAt: Date.now(),
    };
  } catch {
    ssoCache = { config: null, fetchedAt: Date.now() };
  }
  return ssoCache.config;
}

/**
 * Tell the backend to revoke the refresh token's whole session lineage on sign-out.
 *
 * Clearing the NextAuth cookie only makes the browser forget the token; the token
 * itself stayed valid for the rest of its 14 days. `/auth/logout` is idempotent,
 * unauthenticated (the refresh token IS the credential) and always answers 204,
 * so a failure here must never block the sign-out the user asked for.
 */
async function revokeBackendSession(refreshToken: unknown): Promise<void> {
  if (typeof refreshToken !== 'string' || !refreshToken) return;
  try {
    await fetch(`${apiOrigin}/api/v1/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    // Backend unreachable: the cookie is still cleared and the token still
    // expires on its own. Nothing useful to report to a user who is leaving.
  }
}

const baseConfig = {
  session: { strategy: 'jwt' },
  pages: { signIn: '/login' },
  events: {
    async signOut(message) {
      await revokeBackendSession('token' in message ? message.token?.refreshToken : undefined);
    },
  },
  callbacks: {
    // Middleware gate (see middleware.ts matcher, which already excludes /login
    // and /api/auth): every other route requires an authenticated session, so an
    // unauthenticated visitor is redirected to /login instead of landing on an
    // app page that then 401s against the backend.
    authorized({ auth }) {
      return !!auth?.user;
    },
    async jwt({ token, user, account }) {
      // Credentials: the authorize() result already carries backend tokens.
      if (user && 'accessToken' in user) {
        token.accessToken = user.accessToken as string;
        token.refreshToken = user.refreshToken as string;
        token.accessTokenExpires = accessTokenExpiryMs(user.accessToken as string);
        token.organizationId = user.organizationId as string;
        token.roles = user.roles as string[];
        token.name = (user.name as string | undefined) ?? token.name;
        token.email = (user.email as string | undefined) ?? token.email;
        return token;
      }
      // SSO: exchange the IdP's id_token for backend app tokens on first sign-in
      // (the backend re-verifies it against the connection's issuer JWKS).
      if (account?.provider === 'sso' && account.id_token) {
        const tokens = await backendTokens('sso', { id_token: account.id_token });
        if (!tokens) throw new Error('No AequorOS account is provisioned for this identity.');
        const claims = decodeJwt(tokens.access_token);
        token.accessToken = tokens.access_token;
        token.refreshToken = tokens.refresh_token;
        token.accessTokenExpires = accessTokenExpiryMs(tokens.access_token);
        token.organizationId = String(claims.org);
        token.roles = (claims.roles as string[]) ?? [];
        token.sub = String(claims.sub);
        token.name = claims.name ? String(claims.name) : token.name;
        token.email = claims.email ? String(claims.email) : token.email;
        return token;
      }
      // Subsequent calls: hand back the current token while it is still valid,
      // otherwise rotate it via the backend refresh endpoint.
      if (
        token.accessTokenExpires &&
        Date.now() < token.accessTokenExpires - REFRESH_SKEW_MS
      ) {
        return token;
      }
      return refreshAccessToken(token);
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined;
      session.error = token.error;
      if (session.user) {
        session.user.name = (token.name as string | undefined) ?? session.user.name;
        session.user.email = (token.email as string | undefined) ?? session.user.email;
        session.user.organizationId = token.organizationId as string | undefined;
        session.user.roles = (token.roles as string[]) ?? [];
      }
      return session;
    },
  },
} satisfies Omit<NextAuthConfig, 'providers'>;

const credentialsProvider = Credentials({
  name: 'Email and password',
  credentials: { email: {}, password: {} },
  async authorize(credentials) {
    const tokens = await backendTokens('login', {
      email: credentials?.email,
      password: credentials?.password,
    });
    if (!tokens) return null;
    const claims = decodeJwt(tokens.access_token);
    return {
      id: String(claims.sub),
      email: String(claims.email ?? ''),
      name: claims.name ? String(claims.name) : undefined,
      organizationId: String(claims.org),
      roles: (claims.roles as string[]) ?? [],
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    };
  },
});

// Lazy config: the SSO provider is materialized per request, ONLY on auth routes
// (sign-in, callback, providers). Middleware's session gate and server-side
// auth() calls never pay for the backend config fetch.
export const { handlers, signIn, signOut, auth } = NextAuth(async (req) => {
  const providers: NextAuthConfig['providers'] = [credentialsProvider];
  if (req?.nextUrl.pathname.startsWith('/api/auth')) {
    // fetchSsoConfig() has already put the issuer through the egress guard, so
    // reaching this line at all means the destination was permitted.
    const sso = await fetchSsoConfig();
    if (sso?.enabled && sso.issuer && sso.client_id && sso.client_secret) {
      providers.push({
        id: 'sso',
        name: 'SSO',
        type: 'oidc',
        issuer: sso.issuer,
        clientId: sso.client_id,
        clientSecret: sso.client_secret,
        // Every server-side fetch of the OIDC flow — discovery, the
        // token-endpoint exchange, userinfo — is routed through the egress
        // guard here. Checking the issuer alone is NOT enough: the token
        // endpoint is named by the discovery document, i.e. by whatever
        // answered the first request, so it is a second, separately
        // attacker-influenced URL. `customFetch` is the one seam @auth/core
        // threads into all of them (see @auth/core/lib/actions/**), which is
        // why the guard lives at the fetch boundary rather than on the issuer
        // string. Auth.js sets `allowInsecureRequests` internally, so the
        // https allow-list here is the only thing requiring TLS.
        [customFetch]: guardedFetchFor('OIDC endpoint'),
      });
    }
  }
  return { ...baseConfig, providers };
});
