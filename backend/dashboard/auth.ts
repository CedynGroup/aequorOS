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
import NextAuth, { type NextAuthConfig } from 'next-auth';
import Credentials from 'next-auth/providers/credentials';

const apiOrigin = (process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? 'http://localhost:8000')
  .replace(/\/api\/v1\/?$/, '');

/** Decode a JWT payload (no verification — the token was just issued by our backend). */
function decodeJwt(token: string): Record<string, unknown> {
  const payload = token.split('.')[1];
  const json = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString();
  return JSON.parse(json) as Record<string, unknown>;
}

async function backendTokens(path: string, body: unknown) {
  const res = await fetch(`${apiOrigin}/api/v1/auth/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
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
    ssoCache = {
      config: res.ok ? ((await res.json()) as SsoClientConfig) : null,
      fetchedAt: Date.now(),
    };
  } catch {
    ssoCache = { config: null, fetchedAt: Date.now() };
  }
  return ssoCache.config;
}

const baseConfig = {
  session: { strategy: 'jwt' },
  pages: { signIn: '/login' },
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
    const sso = await fetchSsoConfig();
    if (sso?.enabled && sso.issuer && sso.client_id && sso.client_secret) {
      providers.push({
        id: 'sso',
        name: 'SSO',
        type: 'oidc',
        issuer: sso.issuer,
        clientId: sso.client_id,
        clientSecret: sso.client_secret,
      });
    }
  }
  return { ...baseConfig, providers };
});
