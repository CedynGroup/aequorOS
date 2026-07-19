import type { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    accessToken?: string;
    /** Set when a silent token refresh failed — the UI should force re-login. */
    error?: 'RefreshTokenError';
    user: {
      organizationId?: string;
      roles?: string[];
    } & DefaultSession['user'];
  }

  interface User {
    organizationId?: string;
    roles?: string[];
    accessToken?: string;
    refreshToken?: string;
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    /** Epoch ms at which `accessToken` expires (from its `exp` claim). */
    accessTokenExpires?: number;
    organizationId?: string;
    roles?: string[];
    /** Set when a silent token refresh failed — the UI should force re-login. */
    error?: 'RefreshTokenError';
  }
}
