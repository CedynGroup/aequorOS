const SESSION_COOKIE_BASE_NAMES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "__Host-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
  "__Host-next-auth.session-token",
] as const;

const EXPIRED = "Thu, 01 Jan 1970 00:00:00 GMT";

export type SessionCookieGroup = {
  baseName: string;
  cookieNames: string[];
  value: string | null;
};

function parseCookieHeader(cookieHeader: string | null): Map<string, string> {
  const cookies = new Map<string, string>();
  if (!cookieHeader) return cookies;
  for (const part of cookieHeader.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (!name) continue;
    cookies.set(name, part.slice(separator + 1).trim());
  }
  return cookies;
}

function chunkIndex(baseName: string, cookieName: string): number | null {
  if (cookieName === baseName) return null;
  const suffix = cookieName.slice(baseName.length + 1);
  return /^\d+$/.test(suffix) ? Number(suffix) : null;
}

function matchesBaseName(baseName: string, cookieName: string): boolean {
  return (
    cookieName === baseName ||
    (cookieName.startsWith(`${baseName}.`) &&
      chunkIndex(baseName, cookieName) !== null)
  );
}

export function sessionCookieGroups(
  cookieHeader: string | null,
): SessionCookieGroup[] {
  const cookies = parseCookieHeader(cookieHeader);
  const groups: SessionCookieGroup[] = [];

  for (const baseName of SESSION_COOKIE_BASE_NAMES) {
    const cookieNames = [...cookies.keys()].filter((name) =>
      matchesBaseName(baseName, name),
    );
    if (cookieNames.length === 0) continue;

    const hasBaseCookie = cookieNames.includes(baseName);
    const chunkNames = cookieNames
      .filter((name) => name !== baseName)
      .sort(
        (left, right) =>
          chunkIndex(baseName, left)! - chunkIndex(baseName, right)!,
      );
    const completeChunks = chunkNames.every(
      (name, index) => chunkIndex(baseName, name) === index,
    );
    const value =
      hasBaseCookie && chunkNames.length === 0
        ? cookies.get(baseName)!
        : !hasBaseCookie && completeChunks
          ? chunkNames.map((name) => cookies.get(name)!).join("")
          : null;

    groups.push({ baseName, cookieNames, value });
  }

  return groups;
}

export function authSessionCookieNamesToClear(
  cookieHeader: string | null,
): string[] {
  const names = new Set<string>(SESSION_COOKIE_BASE_NAMES);
  for (const group of sessionCookieGroups(cookieHeader)) {
    for (const name of group.cookieNames) names.add(name);
  }
  return [...names];
}

export function presentAuthSessionCookieNames(
  cookieHeader: string | null,
): string[] {
  return sessionCookieGroups(cookieHeader).flatMap(
    (group) => group.cookieNames,
  );
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
