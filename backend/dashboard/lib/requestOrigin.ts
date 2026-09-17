export function requestOrigin(
  request: Pick<Request, "headers" | "url">,
): string {
  const forwardedHost = request.headers
    .get("x-forwarded-host")
    ?.split(",")[0]
    ?.trim();
  const directHost = request.headers.get("host");
  // Next dev injects x-forwarded-host from its bind hostname ("localhost"),
  // which may differ from the browser's Host ("127.0.0.1"). Production runs
  // behind our trusted proxy, where x-forwarded-host is the public authority.
  const host =
    process.env.NODE_ENV === "production"
      ? (forwardedHost ?? directHost)
      : (directHost ?? forwardedHost);
  if (!host) throw new Error("Request host header is missing.");
  const forwardedProto = request.headers
    .get("x-forwarded-proto")
    ?.split(",")[0]
    ?.trim();
  const protocol = forwardedProto ?? "http";
  return `${protocol}://${host}`;
}

export function sessionRedirect(url: string, origin: string): string {
  try {
    const target = new URL(url, origin);
    if (target.origin === origin) return target.href;
  } catch {}
  return origin;
}
