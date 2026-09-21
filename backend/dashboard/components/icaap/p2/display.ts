/**
 * The ONLY numeric literals the P2 and P3 ICAAP components are allowed to
 * carry. (`components/icaap/p3/` imports from here rather than keeping a
 * second exemption: one reviewed file is easier to police than two.)
 *
 * `no-literals.test.ts` walks every other file under `components/icaap/p2/`
 * and `components/icaap/p3/`
 * with the TypeScript compiler and fails on any numeric literal other than 0 or
 * 1 (D-024: a threshold, band, floor or tolerance must arrive on an API
 * payload, never be written into display code). That guard would also catch the
 * harmless presentation numbers — an icon's pixel size, a textarea's row count,
 * how many digits a percentage prints to — so they live here, where each one is
 * visible and reviewable, and this file is the guard's single exemption.
 *
 * NOTHING REGULATORY MAY BE ADDED HERE. Everything below is either a pixel, a
 * character count, or a rounding used purely for reading a figure that has
 * already been decided by the server. A floor, a band edge, a tolerance or a
 * deadline in this file would be a D-024 violation with an alibi.
 */

/** Decimal places a ratio/percentage is PRINTED to. Not a rounding of policy. */
export const PCT_DECIMALS = 2;
/** Decimal places an amount is PRINTED to. */
export const AMOUNT_DECIMALS = 2;

/**
 * Lucide icon sizes, in pixels. Only the two this module actually uses are
 * declared: an unused entry here is an unreviewed exemption waiting for
 * someone to reach for it.
 */
export const ICON_XS = 11;
export const ICON_SM = 13;

/** Textarea heights, in rows. */
export const ROWS_MEDIUM = 3;
export const ROWS_LONG = 5;

/** Character limits mirrored from the API schemas, so the field says so first. */
export const REASON_MAX = 500;
/**
 * The shortest reason the API accepts on a send-back, a freeze or a workflow
 * decision (`Field(min_length=10)` on those schemas).
 *
 * An EDITORIAL limit on a sentence, not a regulatory bound: it exists so that
 * "ok" cannot be the recorded reason a Board's report was sent back. Mirrored
 * here so the form can say so before the server refuses, never instead of it.
 */
export const REASON_MIN = 10;
export const RATIONALE_MAX = 2000;
export const TITLE_MAX = 200;
export const EXPLANATION_MAX = 2000;
export const DESCRIPTION_MAX = 4000;
export const REFERENCE_MAX = 120;
export const ROW_KEY_MAX = 40;
export const COMPONENT_KEY_MAX = 60;

/** How much of a digest is enough to compare two by eye. */
export const DIGEST_CHARS = 12;

/** Indent width for a pretty-printed structured value. A layout, not a bound. */
export const JSON_INDENT = 2;

/** Grid geometry for the materiality heatmap (CSS, not methodology). */
export const MATRIX_CELL_MIN_PX = 56;

/**
 * The percent base, for turning a 0-1 layout fraction into a CSS `left: x%`.
 *
 * It is the same structural constant the backend's `domain/icaap/units.py`
 * registers as `HUNDRED` — arithmetic on the definition of "per cent", not a
 * threshold. It is never used to convert a REGULATORY figure; those arrive
 * already in the unit the API declares for them.
 */
export const PERCENT_BASE = 100;

/**
 * Table layout: how many columns a footer cell spans.
 *
 * A column count, not a quantity. It is here rather than inline because the
 * D-024 guard is deliberately blunt — it cannot tell a colspan from a band
 * edge by looking at the source, and neither can a reviewer reading a diff.
 */
export const COLSPAN_TWO = 2;
