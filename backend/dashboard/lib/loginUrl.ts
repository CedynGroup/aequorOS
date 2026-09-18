const configuredLogin = new URL(
  process.env.NEXT_PUBLIC_LOGIN_URL ?? "/login",
  "http://localhost",
);
export const LOGIN_URL =
  configuredLogin.pathname + configuredLogin.search + configuredLogin.hash;

/** Why the user is back on the sign-in page; the form turns it into a message. */
export type LoginReason = "access_changed" | "session_ended";

export function loginUrlWithReason(
  reason: LoginReason,
  origin?: string,
): string {
  const target = new URL(LOGIN_URL, origin ?? "http://localhost");
  target.searchParams.set("reason", reason);
  return origin ? target.href : target.pathname + target.search + target.hash;
}
