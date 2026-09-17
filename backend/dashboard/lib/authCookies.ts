const SESSION_COOKIE_BASE_NAMES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "__Host-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
  "__Host-next-auth.session-token",
] as const;

const EXPIRED = "Thu, 01 Jan 1970 00:00:00 GMT";

export function authSessionCookieNamesToClear(
  cookieHeader: string | null,
): string[] {
  return [...new Set<string>([
    ...SESSION_COOKIE_BASE_NAMES,
    ...presentAuthSessionCookieNames(cookieHeader),
  ])];
}

export function presentAuthSessionCookieNames(
  cookieHeader: string | null,
): string[] {
  const names = new Set<string>();
  for (const part of cookieHeader?.split(";") ?? []) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (SESSION_COOKIE_BASE_NAMES.some((baseName) =>
      name === baseName ||
      (name.startsWith(`${baseName}.`) && /^\d+$/.test(name.slice(baseName.length + 1))),
    )) names.add(name);
  }
  return [...names];
}

export function expiredAuthSessionCookieHeaders(
  names: Iterable<string>,
): string[] {
  return [...new Set(names)].map((name) => {
    const secure = name.startsWith("__Secure-") || name.startsWith("__Host-");
    return (
      `${name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Expires=${EXPIRED}` +
      (secure ? "; Secure" : "")
    );
  });
}

export async function cleanAuthResponseCookies(
  request: Request,
  response: Response,
): Promise<Response> {
  const action = new URL(request.url).pathname.split("/").pop();
  const issued = new Set<string>();
  for (const header of response.headers.getSetCookie()) {
    const pair = header.split(";")[0];
    for (const name of presentAuthSessionCookieNames(pair)) {
      if (pair.slice(pair.indexOf("=") + 1)) issued.add(name);
      else issued.delete(name);
    }
  }
  let clearAll = false;
  if (response.ok && action === "session") {
    clearAll = (await response.clone().json()) === null;
  }
  if (
    action === "signout" &&
    request.method === "POST" &&
    response.status < 400
  ) {
    const destination = response.headers.get("location") ??
      (response.headers.get("content-type")?.includes("application/json")
        ? (await response.clone().json()).url
        : undefined);
    clearAll = typeof destination === "string" &&
      !new URL(destination, request.url).searchParams.has("error");
  }
  if (!clearAll && issued.size === 0) return response;
  const names = authSessionCookieNamesToClear(request.headers.get("cookie"))
    .filter((name) => clearAll || !issued.has(name));
  for (const header of expiredAuthSessionCookieHeaders(names)) {
    response.headers.append("set-cookie", header);
  }
  return response;
}
