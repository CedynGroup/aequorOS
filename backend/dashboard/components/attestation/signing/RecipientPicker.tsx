'use client';

/**
 * Who signs next, by name.
 *
 * The signing policy says "one approver, holding one of these officer titles".
 * That is the control, and it stays the control — this only fills it with an
 * actual person, so the return lands in a named colleague's queue instead of
 * being finished and forgotten.
 *
 * The roster is NOT pre-filtered down to the people the server would accept.
 * Maker-checker, officer titles, the generated-by control and the distinct-signer
 * rule are all resolved server-side against state this component cannot see, so
 * filtering here would either hide someone who is in fact eligible or — worse —
 * present an empty list with no explanation of who is missing or why. Instead the
 * roster is annotated with what IS knowable from the roster (the platform role),
 * and the server's refusal is surfaced verbatim when it comes.
 */

import type { OrganizationUserRead, SigningRole } from '@aequoros/risk-service-api';
import { UserCheck } from 'lucide-react';
import { roleNoun } from '../shared';

/** Slots whose holder must carry the approver platform role (`routing.CHECKER_ROLES`). */
const CHECKER_ROLES = new Set(['approver', 'board']);

/** Mirrors `security.has_role` — admin outranks approver, so it qualifies too. */
function canHoldCheckerSlot(user: OrganizationUserRead): boolean {
  return user.role === 'approver' || user.role === 'admin';
}

export interface Nomination {
  signingRole: SigningRole;
  userId: string;
}

export default function RecipientPicker({
  slots,
  users,
  nominations,
  onChange,
}: {
  /** One entry per signature still outstanding after the caller signs. */
  slots: SigningRole[];
  users: OrganizationUserRead[];
  nominations: Nomination[];
  onChange: (nominations: Nomination[]) => void;
}) {
  if (slots.length === 0) {
    return (
      <p className="text-caption text-slate leading-relaxed">
        Nobody has to sign after you — your signature completes the policy in
        force for this return, and it becomes cleared to submit.
      </p>
    );
  }

  const roster = [...users]
    .filter((user) => user.isActive)
    .sort((a, b) => {
      const rank = Number(canHoldCheckerSlot(b)) - Number(canHoldCheckerSlot(a));
      if (rank !== 0) return rank;
      return (a.displayName ?? a.email).localeCompare(b.displayName ?? b.email);
    });

  return (
    <div className="space-y-3">
      {slots.map((role, index) => {
        const selected = nominations[index]?.userId ?? '';
        const chosen = roster.find((user) => user.id === selected);
        const mismatch =
          chosen != null && CHECKER_ROLES.has(role) && !canHoldCheckerSlot(chosen);
        return (
          <div key={`${role}-${index}`}>
            <label className="block">
              <span className="block text-caption font-medium text-navy mb-1.5">
                {roleNoun(role)}
                {slots.filter((slot) => slot === role).length > 1 && ` #${index + 1}`}
              </span>
              <select
                value={selected}
                aria-label={`${roleNoun(role)} recipient`}
                onChange={(event) => {
                  const next = slots.map((slotRole, slotIndex) => ({
                    signingRole: slotRole,
                    userId:
                      slotIndex === index
                        ? event.target.value
                        : (nominations[slotIndex]?.userId ?? ''),
                  }));
                  onChange(next.filter((entry) => entry.userId.length > 0));
                }}
                className="w-full rounded border border-border bg-surface px-3 py-2 text-body text-navy"
              >
                <option value="">Select an officer…</option>
                {roster.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.displayName ?? user.email}
                    {user.jobTitle ? ` — ${user.jobTitle}` : ''} ({user.role})
                  </option>
                ))}
              </select>
            </label>
            {mismatch && (
              <p role="status" className="mt-1 text-caption text-warning leading-relaxed">
                {chosen.displayName ?? chosen.email} holds the &apos;{chosen.role}&apos;
                role. The server will refuse this nomination — maker-checker cannot
                be satisfied by a preparer — and the refusal takes your signature
                with it, so pick somebody with the approver role.
              </p>
            )}
          </div>
        );
      })}

      <p className="inline-flex items-start gap-1.5 text-caption text-slate leading-relaxed">
        <UserCheck size={13} className="shrink-0 mt-0.5" aria-hidden />
        They are notified as soon as your signature lands, and the return appears
        in their signature queue. Only a named recipient can then fill the slot —
        signing around a nomination is refused.
      </p>
    </div>
  );
}
