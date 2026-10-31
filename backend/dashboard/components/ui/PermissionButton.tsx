"use client";

import type { ButtonHTMLAttributes } from "react";
import { DisabledWithReason } from "./DisabledWithReason";

/** Keep permission-only refusals visible and explain them on hover and focus. */
export function PermissionButton({
  reason,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { reason?: string }) {
  if (!reason) return <button {...props} disabled={disabled} />;
  return (
    <DisabledWithReason reason={reason}>
      {(descriptionId) => (
        <button {...props} disabled aria-describedby={descriptionId} />
      )}
    </DisabledWithReason>
  );
}
