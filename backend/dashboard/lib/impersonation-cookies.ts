/**
 * Cookie names for the act-as-examiner (operator inspection) hand-off.
 *
 * Kept in a dependency-free module so the edge middleware, the server route
 * handlers, and the client store can all share the exact strings without any of
 * them pulling in browser- or Node-only code.
 *
 * Two cookies, by design:
 *   · aeq-impersonation        — HttpOnly, Secure, SameSite=Lax. Holds the raw
 *     operator impersonation JWT. Client JS can NOT read it (XSS mitigation for
 *     a cross-tenant staff credential); only the server routes below touch it.
 *   · aeq-impersonation-active — NON-HttpOnly companion marker (value "1"). Lets
 *     the client cheaply decide, from document.cookie and with zero network,
 *     whether an inspection hand-off is active. It carries NO token material —
 *     only the fact that one exists — so exposing it to JS leaks nothing.
 *
 * The whole feature is gated on the marker: when it is absent every impersonation
 * code path is a no-op, so the normal tenant login/session/API flow is unchanged.
 */
export const IMPERSONATION_COOKIE = 'aeq-impersonation';
export const IMPERSONATION_MARKER_COOKIE = 'aeq-impersonation-active';
