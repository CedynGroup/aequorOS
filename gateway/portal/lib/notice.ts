import { NOTICES, isNoticeKey } from "./action-state";
import { QUERY_NUMBER, param, type SearchParams } from "./search-params";

/** The success banner for `?notice=` after an action, with a link that dismisses it. */
export function noticeFrom(params: SearchParams, path: string): { message: string; dismissHref: string } | null {
  const key = param(params, "notice");
  if (!isNoticeKey(key)) return null;
  const ref = param(params, "ref");
  let message: string = NOTICES[key];
  if (key === "resubmission_requested" && ref && QUERY_NUMBER.test(ref)) message = `${message} Request ${ref}.`;
  const rest = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (k === "notice" || k === "ref" || v === undefined) continue;
    rest.set(k, Array.isArray(v) ? (v[0] ?? "") : v);
  }
  const query = rest.toString();
  return { message, dismissHref: query ? `${path}?${query}` : path };
}
