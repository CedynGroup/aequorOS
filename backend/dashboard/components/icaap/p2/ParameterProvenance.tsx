"use client";

/**
 * Where a governed number on this screen came from, and how far it can be
 * trusted.
 *
 * D-024 makes every regulatory number a control-plane row. D-039 adds that some
 * of those rows are REPRESENTATIVE calibrations seeded pending confirmation —
 * documented working values, used by the calculations, not yet confirmed by the
 * founder or the regulator. A screen that prints such a number without saying
 * so is making a claim the platform cannot support, so every surface that shows
 * one renders these chips beside it.
 *
 * The component states what the payload says and nothing else: it does not know
 * what any parameter means, and it never decides that an unconfirmed value is
 * fine.
 */

import { BadgeCheck, FlaskConical, HelpCircle } from "lucide-react";
import StatusPill from "@/components/ui/StatusPill";
import type { IcaapParameterUse } from "@/lib/api/icaapRiskCapital";
import { ICON_XS } from "./display";
import { PENDING_CONFIRMATION, REPRESENTATIVE_CALIBRATION } from "./labels";

/** True when any of these parameters is unconfirmed or representative. */
export function hasPendingCalibration(
  uses: readonly IcaapParameterUse[] | null | undefined,
): boolean {
  return (uses ?? []).some(
    (use) => use.representative || use.confirmationStatus === "pending",
  );
}

/**
 * The citation line for one parameter, as the control plane recorded it.
 * Deliberately not reworded: it is the audit trail for the number.
 */
function citationOf(use: IcaapParameterUse): string {
  const effective = use.effectiveFrom ? ` (from ${use.effectiveFrom})` : "";
  return use.sourceCitation
    ? `${use.paramCode}: ${use.sourceCitation}${effective}`
    : `${use.paramCode}${effective}`;
}

export default function ParameterProvenance({
  uses,
  compact = false,
}: {
  /** Nullable on purpose: a payload may omit the list entirely. */
  uses: readonly IcaapParameterUse[] | null | undefined;
  /** Chips only, without the citation list — for a dense table cell. */
  compact?: boolean;
}) {
  const rows = (uses ?? []).filter((use): use is IcaapParameterUse =>
    Boolean(use),
  );
  if (rows.length === 0) return null;

  const representative = rows.filter((use) => use.representative);
  const pending = rows.filter(
    (use) => use.confirmationStatus === "pending" && !use.representative,
  );
  const missing = rows.filter(
    (use) => use.value === null && use.valueJson === null,
  );

  const tooltip = rows.map(citationOf).join("\n");

  return (
    <div className="flex flex-wrap items-center gap-1.5" title={tooltip}>
      {representative.length > 0 && (
        <StatusPill tone="amber">
          <FlaskConical size={ICON_XS} aria-hidden />
          {REPRESENTATIVE_CALIBRATION}
        </StatusPill>
      )}
      {pending.length > 0 && (
        <StatusPill tone="amber">
          <HelpCircle size={ICON_XS} aria-hidden />
          {PENDING_CONFIRMATION}
        </StatusPill>
      )}
      {missing.length > 0 && (
        <StatusPill tone="critical">
          No governed value for {missing.map((use) => use.paramCode).join(", ")}
        </StatusPill>
      )}
      {representative.length === 0 &&
        pending.length === 0 &&
        missing.length === 0 && (
          <StatusPill tone="slate">
            <BadgeCheck size={ICON_XS} aria-hidden />
            Confirmed calibration
          </StatusPill>
        )}
      {!compact && (
        <ul className="w-full list-none space-y-0.5 text-caption text-slate">
          {rows.map((use) => (
            <li key={use.paramCode}>{citationOf(use)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The one-line caption a panel puts under a figure whose calibration is not
 * confirmed. Returns null when nothing needs saying, so a confirmed screen
 * carries no noise.
 */
export function PendingCalibrationNote({
  uses,
}: {
  uses: readonly IcaapParameterUse[] | null | undefined;
}) {
  if (!hasPendingCalibration(uses)) return null;
  return (
    <p className="mt-2 text-caption text-slate">
      Some values on this panel come from calibrations that are still{" "}
      {PENDING_CONFIRMATION}. They are used by the calculations and are recorded
      in the audit trail, but they have not been confirmed against a published
      source.
    </p>
  );
}
