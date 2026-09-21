/**
 * The ONLY numeric literals the IRRBB Standardised Framework components are
 * allowed to carry.
 *
 * `components/icaap/p2/no-literals.test.ts` walks every other file under
 * `components/irr/sf/`, plus `components/irr/StandardisedFramework.tsx`, with
 * the TypeScript compiler and fails on any numeric literal other than 0 or 1
 * (D-024: a threshold, shock, cap, band or tolerance must arrive on an API
 * payload, never be written into display code). That guard would also catch
 * the harmless presentation numbers — an icon's pixel size, how many digits a
 * percentage prints to — so they live here, where each one is visible and
 * reviewable, and this file is that scan's single exemption.
 *
 * NOTHING REGULATORY MAY BE ADDED HERE. Everything below is either a pixel or
 * a rounding used purely for reading a figure the server has already decided.
 * A shock size, an outlier threshold, a core-deposit cap or a commencement
 * date in this file would be a D-024 violation with an alibi — and the
 * framework's own numbers are precisely the ones a bank would be judged on.
 *
 * It deliberately does not re-export `components/icaap/p2/display.ts`: that
 * file is the ICAAP workspace's reviewed exemption, and one module reaching
 * into another's would make both harder to police.
 */

/** Decimal places a ratio or percentage is PRINTED to. Not a rounding of policy. */
export const PCT_DECIMALS = 2;

/** Decimal places a repricing maturity, in years, is PRINTED to. */
export const YEARS_DECIMALS = 2;

/** Lucide icon sizes, in pixels. Only the sizes this module uses are declared. */
export const ICON_XS = 12;
export const ICON_SM = 14;

/** How much of an input hash is enough to compare two runs by eye. */
export const DIGEST_CHARS = 12;

/**
 * Table layout: how many columns a spanning cell covers.
 *
 * A column count, not a quantity. It is here rather than inline because the
 * D-024 guard is deliberately blunt — it cannot tell a colspan from a shock
 * size by looking at the source, and neither can a reviewer reading a diff.
 */
export const COLSPAN_TWO = 2;
export const COLSPAN_THREE = 3;
export const COLSPAN_FOUR = 4;
export const COLSPAN_SIX = 6;
