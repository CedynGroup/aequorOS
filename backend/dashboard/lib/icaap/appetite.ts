/**
 * The client-side MIRROR of the appetite ordering rule.
 *
 * The server is the authority. `PUT .../appetite/metrics/{id}` answers 422
 * `appetite_ordering_invalid {violations, reference}` and that response is what
 * the screen shows; this module exists only so a preparer sees the problem
 * while they are typing rather than after they submit.
 *
 * Three things it deliberately does NOT do:
 *
 * 1. **It states no threshold.** Every number it compares is one the operator
 *    entered or one the API sent (`regulatoryReference.value`). There is no
 *    floor, cap or band in this file — D-024.
 * 2. **It never invents a comparison.** A level that was not entered, and a
 *    regulatory reference the control plane does not govern, are skipped. An
 *    ungoverned reference is D-036: the capacity check reads "not assessed
 *    against a regulatory floor", which is a valid answer, not a failure.
 * 3. **It never overrides the server.** `checkAppetiteOrdering` returning no
 *    violations does not mean the save will succeed.
 *
 * Kept React-free and free of runtime imports beyond `numOrNull`, so
 * `pnpm --filter @aequoros/dashboard test` can compile and run it under node.
 */

import { numOrNull } from "../api/values";

export type AppetiteDirection = "higher_is_safer" | "lower_is_safer";

/** The four levels of one metric, as strings exactly as they arrive/are typed. */
export interface AppetiteLevelInput {
  appetite?: string | number | null;
  tolerance?: string | number | null;
  capacity?: string | number | null;
  /** The governed regulatory value, or null when none is governed (D-036). */
  reference?: string | number | null;
}

export interface AppetiteLevels {
  appetite: number | null;
  tolerance: number | null;
  capacity: number | null;
  reference: number | null;
}

export type AppetiteViolationCode =
  | "appetite_beyond_tolerance"
  | "tolerance_beyond_capacity"
  | "capacity_beyond_regulatory_reference";

export interface AppetiteViolation {
  code: AppetiteViolationCode;
  /** Plain-sentence copy. The server's own message wins when it sends one. */
  message: string;
}

export interface AppetiteOrderingResult {
  violations: AppetiteViolation[];
  /**
   * False when no regulatory reference is governed for this metric. The screen
   * must then say the capacity is "not assessed against a regulatory floor"
   * (D-036) — never substitute a number, never treat it as a pass.
   */
  referenceAssessed: boolean;
}

/** Null-preserving parse of every level. Absence stays absence. */
export function parseLevels(input: AppetiteLevelInput): AppetiteLevels {
  return {
    appetite: numOrNull(input.appetite),
    tolerance: numOrNull(input.tolerance),
    capacity: numOrNull(input.capacity),
    reference: numOrNull(input.reference),
  };
}

/**
 * Is `inner` at least as conservative as `outer`, for this direction?
 *
 * For a metric where a higher value is safer (a capital ratio), the safer level
 * is the larger one, so the ordering runs appetite >= tolerance >= capacity.
 * For one where a lower value is safer (an NPL limit), it runs the other way.
 * Equality is allowed: a bank may set its appetite exactly at its tolerance.
 */
function atLeastAsConservative(
  inner: number,
  outer: number,
  direction: AppetiteDirection,
): boolean {
  return direction === "higher_is_safer" ? inner >= outer : inner <= outer;
}

/**
 * Check one metric's levels against the ordering rule.
 *
 * Only pairs where BOTH values are present are compared; a half-filled form
 * produces no violation, because "not entered yet" is not "wrong".
 */
export function checkAppetiteOrdering(
  direction: AppetiteDirection,
  input: AppetiteLevelInput,
): AppetiteOrderingResult {
  const levels = parseLevels(input);
  const violations: AppetiteViolation[] = [];

  if (
    levels.appetite !== null &&
    levels.tolerance !== null &&
    !atLeastAsConservative(levels.appetite, levels.tolerance, direction)
  ) {
    violations.push({
      code: "appetite_beyond_tolerance",
      message:
        "Appetite is further from safety than tolerance. Appetite is the level the bank " +
        "intends to operate at, so it sits on the safe side of tolerance.",
    });
  }

  if (
    levels.tolerance !== null &&
    levels.capacity !== null &&
    !atLeastAsConservative(levels.tolerance, levels.capacity, direction)
  ) {
    violations.push({
      code: "tolerance_beyond_capacity",
      message:
        "Tolerance is further from safety than capacity. Capacity is the most the bank " +
        "could absorb, so tolerance sits on the safe side of it.",
    });
  }

  const referenceAssessed = levels.reference !== null;
  if (
    referenceAssessed &&
    levels.capacity !== null &&
    !atLeastAsConservative(levels.capacity, levels.reference as number, direction)
  ) {
    violations.push({
      code: "capacity_beyond_regulatory_reference",
      message:
        "Capacity is set beyond the governing regulatory value for this metric. " +
        "The bank cannot declare a capacity the regulation does not permit.",
    });
  }

  return { violations, referenceAssessed };
}

// ---------------------------------------------------------------------------
// Scale geometry (presentation only)
// ---------------------------------------------------------------------------

export interface ScaleDomain {
  min: number;
  max: number;
}

/**
 * The drawing domain for one metric's scale: the span of whatever values exist,
 * padded so the outermost marker is not on the edge.
 *
 * Returns null when fewer than two distinct values exist — there is no scale to
 * draw, and inventing one would imply a spread the data does not have.
 */
export function scaleDomain(
  values: readonly (number | null)[],
  padFraction = 0.12,
): ScaleDomain | null {
  const present = values.filter((v): v is number => v !== null);
  if (present.length === 0) return null;
  const min = Math.min(...present);
  const max = Math.max(...present);
  if (min === max) return null;
  const pad = (max - min) * padFraction;
  return { min: min - pad, max: max + pad };
}

/**
 * Where a value sits on the drawn scale, as a 0–1 fraction from the LEFT, with
 * the safe side on the right.
 *
 * For `lower_is_safer` the axis is reversed, so a lower (safer) value is drawn
 * further right — the reader's "safe is right" intuition holds for both kinds
 * of metric without them having to check which way the numbers run.
 */
export function scaleFraction(
  value: number,
  domain: ScaleDomain,
  direction: AppetiteDirection,
): number {
  const span = domain.max - domain.min;
  if (span === 0) return 0;
  const raw = (value - domain.min) / span;
  const oriented = direction === "higher_is_safer" ? raw : 1 - raw;
  return Math.min(1, Math.max(0, oriented));
}
