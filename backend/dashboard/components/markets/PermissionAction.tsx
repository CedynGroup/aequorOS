'use client';

/**
 * A Markets action that stays visible when the user lacks the exact grant.
 *
 * The permission-only disabled-control policy (docs/rbac.md): a control the
 * user cannot use is rendered disabled, and its tooltip names the required
 * product permission and who can grant it. Structural exclusions stay hidden
 * elsewhere; this component is only for permission gaps.
 */

import type { ReactNode } from 'react';
import { DisabledWithReason } from '@/components/ui/DisabledWithReason';

export default function PermissionAction({
  reason,
  disabled = false,
  onClick,
  className,
  children,
}: {
  /** The grant sentence the user is missing, or undefined when authorized. */
  reason?: string;
  /** A non-permission reason to disable (pending mutation, empty form). */
  disabled?: boolean;
  onClick: () => void;
  className: string;
  children: ReactNode;
}) {
  const button = (descriptionId?: string) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || reason !== undefined}
      aria-describedby={descriptionId}
      className={`${className} disabled:cursor-not-allowed disabled:opacity-50`}
    >
      {children}
    </button>
  );
  if (!reason) return button();
  return (
    <DisabledWithReason reason={reason}>
      {(descriptionId) => button(descriptionId)}
    </DisabledWithReason>
  );
}
