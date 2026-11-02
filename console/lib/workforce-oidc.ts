/** Google policy may be exercised with a stand-in issuer only on local/test. */
export function usesGooglePolicy(
  issuer: string,
  env: Readonly<Record<string, string | undefined>> = process.env,
): boolean {
  const testMode = ["1", "true", "yes", "on"].includes(
    env.OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE?.trim().toLowerCase() ?? "",
  );
  if (testMode && !["local", "test"].includes(env.APP_ENV ?? "")) {
    throw new Error(
      "OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE is limited to local/test.",
    );
  }
  return issuer === "https://accounts.google.com" || testMode;
}

export function oidcSessionMaxAge(
  issuer: string,
  env: Readonly<Record<string, string | undefined>> = process.env,
): number {
  if (!usesGooglePolicy(issuer, env)) return 0;
  const raw = env.OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS?.trim();
  if (!raw || raw === "0") return 0;
  const seconds = Number(raw);
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 43200) {
    throw new Error("Invalid OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS.");
  }
  return seconds;
}

export function applyWorkforcePolicy(
  url: URL,
  issuer: string,
  env: Readonly<Record<string, string | undefined>> = process.env,
): void {
  if (!usesGooglePolicy(issuer, env)) return;
  const domain = env.OPERATOR_OIDC_ALLOWED_DOMAIN?.trim() || "aequoros.com";
  url.searchParams.set("hd", domain);
}

/** Cookie expiry mirrors the API's cap on the signed token's original iat. */
export function oidcSessionExpiry(
  exp: number,
  issued: unknown,
  limit: number,
  now = Math.floor(Date.now() / 1000),
): number | null {
  if (!limit) return exp > now ? exp : null;
  if (
    typeof issued !== "number" ||
    !Number.isInteger(issued) ||
    issued < 0 ||
    issued > now
  ) {
    return null;
  }
  const expires = Math.min(exp, issued + limit);
  return expires > now ? expires : null;
}
