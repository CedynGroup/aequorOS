/**
 * Reading `GET /api/v1/feature-flags` for the flags the generated client predates.
 *
 * Pure, dependency-free and deliberately tiny, for two separate reasons:
 *
 * * it is proved by `ask.test.ts` in plain Node, which cannot load a module that
 *   imports React, the generated client or `fetch`; and
 * * it is reached from the app SHELL — `BankContext` builds the module scope on
 *   every page — so whatever it imports lands in the Command Center's initial
 *   bundle, which the home-route bundle guard exists to protect. Putting this one
 *   function beside the rest of the natural-language reader cost 1.2 KB gzip on
 *   every page in the product for a surface almost nobody opens. Measured, not
 *   assumed: 870,995 B raw without it, 874,397 B with.
 */

/**
 * Whether THIS deployment serves natural-language questions.
 *
 * Three-valued, and the middle value carries the whole design: `undefined` means
 * the platform has not said, `false` means it said no, `true` means it said yes.
 * Navigation hides on anything but `true` so no door is offered before the answer
 * arrives; the route guard refuses only on `false` so a deep-link refresh does not
 * 404 while the flag is resolving.
 *
 * WHY THE WIRE NAME. `bi_nlq_enabled` is read off the raw body rather than a
 * generated property, because `FeatureFlagsRead` was generated before the flag
 * existed. That is safe in exactly one direction and it is this one:
 * `FeatureFlagsReadFromJSON` opens with `...json`, so a field the generator did
 * not know about SURVIVES on the parsed object under its snake_case wire name.
 * (The reverse — a request field — is silently dropped, which is why nothing in
 * this feature posts through a generated serializer.) A deployment whose backend
 * does not project the flag yet therefore reads as `undefined`, which hides the
 * surface: fail closed, with no false 404.
 */
export function nlqEnabledFromFeatureFlags(
  flags: unknown,
): boolean | undefined {
  if (flags === null || typeof flags !== "object") return undefined;
  const value = (flags as Record<string, unknown>).bi_nlq_enabled;
  return typeof value === "boolean" ? value : undefined;
}
