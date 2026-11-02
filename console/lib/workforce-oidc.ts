/** Staged OIDC policy shared by the login and callback routes. */
export function oidcSessionMaxAge(
  env: Readonly<Record<string, string | undefined>> = process.env,
): number {
  const raw = env.OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS?.trim();
  if (!raw || raw === "0") return 0;
  const seconds = Number(raw);
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 43200) {
    throw new Error("Invalid OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS.");
  }
  return seconds;
}

function enabled(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes(value?.trim().toLowerCase() ?? "");
}

export function applyWorkforcePolicy(
  url: URL,
  issuer: string,
  reauthenticate: boolean,
  env: Readonly<Record<string, string | undefined>> = process.env,
): void {
  const domain = env.OPERATOR_OIDC_ALLOWED_DOMAIN?.trim();
  if (
    domain &&
    (issuer === "https://accounts.google.com" ||
      enabled(env.OPERATOR_OIDC_REQUIRE_HOSTED_DOMAIN))
  ) {
    url.searchParams.set("hd", domain);
  }
  if (
    reauthenticate ||
    enabled(env.OPERATOR_OIDC_REQUIRE_ASSURANCE) ||
    Number(env.OPERATOR_OIDC_STEP_UP_MAX_AGE_SECONDS ?? "0") > 0
  ) {
    url.searchParams.set(
      "claims",
      JSON.stringify({
        id_token: {
          auth_time: { essential: true },
          amr: { essential: true },
          acr: null,
        },
      }),
    );
  }
  if (reauthenticate) {
    // Google reports auth_time but does not support forcing Account reauth.
    // The API still requires recent evidence; an unchanged auth_time refuses.
    if (issuer !== "https://accounts.google.com") {
      url.searchParams.set("max_age", "0");
      url.searchParams.set("prompt", "login");
    }
  }
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
