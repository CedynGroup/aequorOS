/** Only same-site relative paths may be used as a post-sign-in destination. */
export function safeReturnTo(value: string | null | undefined, fallback = "/"): string {
  if (!value || typeof value !== "string") return fallback;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if ([...value].some((ch) => ch.charCodeAt(0) < 0x20 || ch.charCodeAt(0) === 0x7f)) return fallback;
  if (value.startsWith("/auth/") || value.startsWith("/sign-in")) return fallback;
  return value;
}
