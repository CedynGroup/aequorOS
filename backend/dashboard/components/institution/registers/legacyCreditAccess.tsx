'use client';

// D17: preserve the PR-base CREDIT gate until the backend configure cutover in m8.
import { useSession } from 'next-auth/react';
import { Pencil } from 'lucide-react';

/** Client-side mirror of the server's approver gate (approver or admin). */
export function useApproverGate(): { isApprover: boolean } {
  const { data: session } = useSession();
  const roles = session?.user?.roles ?? [];
  return { isApprover: roles.includes('approver') || roles.includes('admin') };
}

/**
 * The "Edit register" action for a SectionCard header: enabled for approvers,
 * disabled-with-reason for everyone else (the server enforces the same gate).
 */
export function EditRegisterAction({
  isApprover,
  editing,
  onEdit,
  label = 'Edit register',
}: {
  isApprover: boolean;
  editing: boolean;
  onEdit: () => void;
  label?: string;
}) {
  if (editing) return null;
  if (!isApprover) {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          type="button"
          disabled
          title="Requires the approver role"
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md opacity-60 cursor-not-allowed"
        >
          <Pencil size={13} aria-hidden />
          {label}
        </button>
        <span className="text-micro text-slate">Requires the approver role</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onEdit}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
    >
      <Pencil size={13} aria-hidden />
      {label}
    </button>
  );
}
