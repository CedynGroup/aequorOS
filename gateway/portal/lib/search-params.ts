/** Helpers for page `searchParams` (a plain object of string | string[] | undefined). */

export type SearchParams = Record<string, string | string[] | undefined>;

export function param(params: SearchParams, key: string): string | undefined {
  const value = params[key];
  const first = Array.isArray(value) ? value[0] : value;
  const trimmed = first?.trim();
  return trimmed ? trimmed : undefined;
}

export function oneOf<T extends string>(value: string | undefined, allowed: readonly T[], fallback: T): T {
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

/** Builds `path?…` from the current params with some keys replaced or removed (undefined). */
export function hrefWith(path: string, current: Record<string, string | undefined>, changes: Record<string, string | undefined>): string {
  const merged = { ...current, ...changes };
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(merged)) {
    if (value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}

// The API numbers these with a minimum width (sequences.py: 07d, 04d, 04d), so a
// busy year runs past it; match "at least", never "exactly".
export const RECEIPT_ID = /^SRG-\d{2}-\d{7,}$/;
export const QUERY_NUMBER = /^QRY-\d{4}-\d{4,}$/;
export const ACCESS_NUMBER = /^SUP-\d{4}-\d{4,}$/;
export const INSTITUTION_CODE = /^\d{4}$/;
export const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Where an audit entry's object reference leads in the portal, if anywhere. */
export function objectHref(ref: string): string | null {
  if (RECEIPT_ID.test(ref)) return `/submissions/${ref}`;
  if (QUERY_NUMBER.test(ref)) return `/queries/${ref}`;
  if (ACCESS_NUMBER.test(ref)) return "/access";
  if (INSTITUTION_CODE.test(ref)) return `/institutions/${ref}`;
  return null;
}
