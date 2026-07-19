import type { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    accessToken?: string;
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
    organizationId?: string;
    roles?: string[];
  }
}
