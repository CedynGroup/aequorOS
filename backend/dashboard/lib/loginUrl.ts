/**
 * Where unauthenticated users go to sign in.
 *
 * Production serves the login page at the ROOT of the main domain
 * (https://aequoros.com/login — the marketing app rewrites /login to this
 * dashboard's login page), while the dashboard itself lives under /dashboard.
 * Set NEXT_PUBLIC_LOGIN_URL to that absolute root URL in production; in dev it
 * defaults to the app-local /login.
 */
export const LOGIN_URL = process.env.NEXT_PUBLIC_LOGIN_URL ?? '/login';
