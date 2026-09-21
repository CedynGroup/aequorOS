/**
 * Display helpers for the ICAAP workspace.
 *
 * Two rules govern everything here.
 *
 * D-024 — NO REGULATORY NUMBER IN CODE. Every threshold, limit, deadline and
 * traffic-light band displayed by this module arrives on an API payload. There
 * is no local RAG arithmetic, no amber window in days, no floor, no "assumed"
 * minimum. When the server sends nothing, the screen says so.
 *
 * FAIL CLOSED ON ABSENCE. A fact with a null value renders "Not available" and
 * never 0: on screen a fabricated zero is indistinguishable from a measured
 * one, and it compares below every floor.
 *
 * Jurisdiction comes from `lib/format.ts`, which `BankContext` binds to the
 * active bank — never a currency or regulator literal.
 */

import { fmtCurrency, fmtInt, fmtPct } from "@/lib/format";
import { fmtDateUTC, fmtTimestamp, numOrNull } from "@/lib/api/values";
import type {
  IcaapBlockStatus,
  IcaapCycleKind,
  IcaapFactValueRead,
} from "@/lib/api/icaap";

/** What the screen prints where a figure could not be obtained. */
export const NOT_AVAILABLE = "Not available";

/**
 * The generated client materialises a NON-nullable date as `Date` and a
 * nullable one as an ISO `string` (an `anyOf[date, null]` degrades to a plain
 * string). Both arrive here; neither is ever invented.
 */
export function toDate(value: Date | string | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

/** "31 Dec 2025", or the fallback when the date is genuinely absent. */
export function fmtDateValue(
  value: Date | string | null | undefined,
  fallback = "\u2014",
): string {
  const parsed = toDate(value);
  return parsed ? fmtDateUTC(parsed) : fallback;
}

/** "31 Dec 2025 14:02", or the fallback. */
export function fmtTimestampValue(
  value: Date | string | null | undefined,
  fallback = "\u2014",
): string {
  const parsed = toDate(value);
  return parsed ? fmtTimestamp(parsed) : fallback;
}

/**
 * One fact of a bound block, formatted for display.
 *
 * Values arrive as strings (the backend's Decimals, ISO dates and
 * `"true"`/`"false"`). A value that will not parse is absent, not zero.
 */
export function fmtFact(fact: IcaapFactValueRead | undefined): string {
  if (fact?.value === null || fact?.value === undefined || fact.value === "") {
    return NOT_AVAILABLE;
  }
  const raw: string = fact.value;
  switch (fact.kind) {
    case "ratio_pct": {
      const parsed = numOrNull(raw);
      return parsed === null ? NOT_AVAILABLE : fmtPct(parsed);
    }
    case "amount": {
      const parsed = numOrNull(raw);
      return parsed === null
        ? NOT_AVAILABLE
        : fmtCurrency(parsed, fact.currency ?? undefined);
    }
    case "count": {
      const parsed = numOrNull(raw);
      return parsed === null ? NOT_AVAILABLE : fmtInt(parsed);
    }
    case "years": {
      const parsed = numOrNull(raw);
      return parsed === null ? NOT_AVAILABLE : `${parsed} years`;
    }
    case "multiplier": {
      const parsed = numOrNull(raw);
      return parsed === null ? NOT_AVAILABLE : `${parsed}×`;
    }
    case "boolean":
      if (raw === "true") return "Yes";
      if (raw === "false") return "No";
      return NOT_AVAILABLE;
    case "date":
      return fmtDateValue(raw, NOT_AVAILABLE);
    default:
      return raw;
  }
}

/**
 * A block's display name. The contract types the bank's own override as
 * nullable and the platform's name (`spec.title`) as required, so the fallback
 * is the platform's, never a raw block type.
 */
export function blockTitle(block: {
  title?: string | null;
  blockType?: string;
  spec?: { title?: string };
}): string {
  return block.title ?? block.spec?.title ?? block.blockType ?? "Figures";
}

export type BlockStatusTone = "ok" | "warn" | "crit" | "neutral";

/**
 * Plain-sentence status for a data block. No raw enum ever reaches the screen
 * (#203), and the copy says what the preparer must DO, not what the column is
 * called.
 */
export function blockStatusCopy(
  status: IcaapBlockStatus,
  options: { asOf?: string; pinReason?: string | null } = {},
): { label: string; tone: BlockStatusTone } {
  switch (status) {
    case "fresh":
      return { label: "Up to date", tone: "ok" };
    case "stale":
      return { label: "Newer figures available", tone: "warn" };
    case "as_of_mismatch":
      return {
        label: options.asOf
          ? `Figures are not as at ${options.asOf}`
          : "Figures are not as at the cycle date",
        tone: "crit",
      };
    case "source_withdrawn":
      return { label: "Source data was withdrawn", tone: "crit" };
    case "source_missing":
      return { label: "Source no longer available", tone: "crit" };
    case "pinned":
      return {
        label: options.pinReason
          ? `Kept at earlier figures: ${options.pinReason}`
          : "Kept at earlier figures",
        tone: "warn",
      };
    case "unbound":
    default:
      return { label: "Not linked yet", tone: "neutral" };
  }
}

/** The kinds P1 offers when creating a cycle (DV-007 §5 hides the other two). */
export const P1_CYCLE_KINDS: readonly IcaapCycleKind[] = ["annual", "rehearsal"];

export function cycleKindLabel(kind: IcaapCycleKind): string {
  switch (kind) {
    case "annual":
      return "Annual";
    case "rehearsal":
      return "Rehearsal";
    case "material_change":
      return "Material change";
    case "regulator_request":
      return "Requested by the regulator";
    default:
      return kind;
  }
}

/**
 * D-029. A rehearsal goes through the full lifecycle, so it must be labelled
 * wherever it appears — the one sentence that stops a practice run being
 * mistaken for a filing.
 */
export const REHEARSAL_NOTICE =
  "Rehearsal — not a regulatory filing. It is never counted against a filing obligation.";

export function basisLabel(basis: string): string {
  if (basis === "solo") return "Solo";
  if (basis === "consolidated") return "Consolidated";
  return basis;
}

export function cycleStatusLabel(status: string): string {
  switch (status) {
    case "draft":
      return "Draft";
    case "in_review":
      return "In review";
    case "returned":
      return "Returned for changes";
    case "frozen":
      return "Frozen";
    case "board_approved":
      return "Board approved";
    case "submitted":
      return "Submitted";
    case "acknowledged":
      return "Acknowledged";
    case "superseded":
      return "Superseded";
    case "archived":
      return "Archived";
    default:
      return status;
  }
}

/**
 * A section whose primary regulator text has not been read yet (D-006). The
 * checklist for it is incomplete, and a non-rehearsal cycle cannot be frozen
 * while any section is in this state.
 */
export function sourceStatusNote(
  sourceStatus: string,
  regulator: string,
): string | null {
  return sourceStatus === "pending_primary_text"
    ? `Checklist incomplete — pending ${regulator} text`
    : null;
}

export function attachmentGateLabel(gate: string): string {
  switch (gate) {
    case "freeze":
      return "Needed before freezing";
    case "submission":
      return "Needed before submission";
    case "per_block":
      return "Needed for the figures it evidences";
    case "optional":
    default:
      return "Optional";
  }
}

/** "a1b2c3d4…" — enough of a hash to compare two documents by eye. */
export function shortHash(value: string): string {
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}
