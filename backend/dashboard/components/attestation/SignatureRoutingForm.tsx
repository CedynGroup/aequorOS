"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  AttestationStatusRead,
  SigningRole,
} from "@aequoros/risk-service-api";
import { UserCheck } from "lucide-react";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  useOrganizationUsers,
  useUpdatePackageSignatureRouting,
} from "@/lib/api/hooks";
import { hasAccountDirectoryAuthority } from "@/lib/api/accountAdministration";
import { isApiError } from "@/lib/api/client";
import RecipientPicker, {
  type Nomination,
} from "./signing/RecipientPicker";

function routedNominations(
  status: AttestationStatusRead,
  slots: readonly SigningRole[],
): Nomination[] {
  const pending = status.recipients.filter(
    (recipient) => recipient.status === "pending",
  );
  return slots.map((role) => {
    const match = pending.find((recipient) => recipient.signingRole === role);
    if (match) pending.splice(pending.indexOf(match), 1);
    return { signingRole: role, userId: match?.recipientUserId ?? "" };
  });
}

/**
 * Initial routing and authorized reassignment happen before preview or step-up.
 * The recipient API remains authoritative for every nominee and every change.
 */
export default function SignatureRoutingForm({
  bankId,
  packageId,
  status,
}: {
  bankId: string;
  packageId: string;
  status: AttestationStatusRead;
}) {
  const { effectiveAuthority } = useUserProfile();
  const directoryAvailable = hasAccountDirectoryAuthority(effectiveAuthority);
  const usersQuery = useOrganizationUsers(directoryAvailable);
  const updateRouting = useUpdatePackageSignatureRouting(bankId);
  const slots = useMemo<SigningRole[]>(
    () =>
      status.outstanding.flatMap((slot) =>
        Array.from({ length: slot.count }, () => slot.role),
      ),
    [status.outstanding],
  );
  const [nominations, setNominations] = useState<Nomination[]>(() =>
    routedNominations(status, slots),
  );
  const [reason, setReason] = useState("");

  useEffect(() => {
    setNominations(routedNominations(status, slots));
  }, [status, slots]);

  const complete =
    nominations.length === slots.length &&
    nominations.every((nomination) => nomination.userId.trim().length > 0);
  const pending = updateRouting.isPending;
  const error = updateRouting.error;
  const errorMessage =
    isApiError(error) || error instanceof Error
      ? error.message
      : error == null
        ? ""
        : String(error);

  return (
    <form
      aria-label="Signature routing"
      className="space-y-3 rounded-md border border-border-light bg-slate-50 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!complete || !reason.trim() || pending) return;
        updateRouting.mutate({
          packageId,
          recipients: nominations,
          reason: reason.trim(),
        });
      }}
    >
      <div className="flex items-start gap-2">
        <UserCheck size={16} className="mt-0.5 shrink-0 text-action" />
        <p className="text-caption text-slate leading-relaxed">
          An authorized approver must name every outstanding signer before
          preview, step-up or certification. Saving a changed nominee is an
          audited reroute; certification reuses these exact recipients.
        </p>
      </div>
      <RecipientPicker
        slots={slots}
        users={usersQuery.data?.users ?? []}
        nominations={nominations}
        onChange={setNominations}
        directoryAvailable={directoryAvailable}
        notificationPoint="routing"
      />
      <label className="block">
        {'Reason '}
        <span className="font-normal text-slate">(recorded, required)</span>
        <textarea
          aria-label="Routing reason"
          value={reason}
          rows={2}
          maxLength={500}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1.5 w-full rounded-sm border border-border bg-surface px-3 py-2 text-body text-navy"
        />
      </label>
      {errorMessage ? (
        <p role="alert" className="text-caption text-critical">
          {errorMessage}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={!complete || !reason.trim() || pending}
        className="rounded-md bg-action px-3 py-1.5 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50"
      >
        {pending ? "Saving signature routing…" : "Save signature routing"}
      </button>
    </form>
  );
}
