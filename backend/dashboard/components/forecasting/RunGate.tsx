"use client";

/**
 * Wraps a Forecasting execution control in the fleet's permission-only
 * disabled treatment: a user who can see the workspace but holds no
 * Forecasting · Confidential · Run authority keeps the control visible,
 * disabled, and explained. The render prop receives the tooltip's id so the
 * native button can describe itself with the reason.
 */

import type { ReactNode } from "react";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { FORECASTING_CONFIDENTIAL_RUN_REASON } from "@/lib/modules";

export default function ForecastingRunGate({
  canRun,
  className,
  children,
}: {
  canRun: boolean;
  /** Applied to the disabled wrapper, e.g. `w-full` for a block-level control. */
  className?: string;
  children: (descriptionId?: string) => ReactNode;
}) {
  if (canRun) return <>{children()}</>;
  return (
    <DisabledWithReason
      reason={FORECASTING_CONFIDENTIAL_RUN_REASON}
      className={className}
    >
      {(descriptionId) => children(descriptionId)}
    </DisabledWithReason>
  );
}
