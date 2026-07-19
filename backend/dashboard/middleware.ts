export { auth as middleware } from '@/auth';

// Protect everything except the login page, the NextAuth routes, and static assets.
export const config = {
  matcher: ['/((?!login|api/auth|_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
