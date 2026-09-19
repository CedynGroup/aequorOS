"use client";

/**
 * The signed-in account and its permanent signer identity.
 *
 * Personal, not organizational: every active user must reach it, whatever
 * they administer. It renders on Profile & preferences (the personal page any
 * session can open) and on the organization Settings hub for owners. It used
 * to live on the hub alone, so once the hub required Account administration
 * (2026-09-08) an analyst could no longer see their own signer ID — the very
 * string stamped on every document they certify.
 */

import { useSession } from "next-auth/react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import CopyButton from "@/components/ui/CopyButton";
import StatusPill from "@/components/ui/StatusPill";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useMySignerIdentity } from "@/lib/api/hooks";
import { fmtRelative } from "@/lib/api/values";
import {
  avatarColor,
  canHoldSignerIdentity,
  initialsFrom,
  roleLabel,
} from "@/lib/api/identity";

/** A copyable identifier row for the identity grid. */
export function IdField({
  label,
  value,
  wide = true,
}: {
  label: string;
  value: string | undefined | null;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-1 flex items-center gap-2">
        <code className="font-mono text-caption text-navy break-all">
          {value ?? "—"}
        </code>
        {value && (
          <CopyButton text={value} label={label} className="shrink-0" />
        )}
      </dd>
    </div>
  );
}

export default function CurrentAccountPanel() {
  const { data: session } = useSession();
  const { profile } = useUserProfile();
  const email = profile?.email ?? session?.user?.email ?? "";
  const name =
    profile?.displayName || session?.user?.name || email || "Signed in";
  const roles = session?.user?.roles ?? [];
  const role = profile?.role
    ? roleLabel(profile.role)
    : roles.length
      ? roleLabel(roles[0])
      : "Signed in";
  const avatarBackground = avatarColor(profile?.userId ?? email);

  return (
    <Card>
      <CardHeader
        title="Your account"
        action={<StatusPill tone="success">You</StatusPill>}
      />
      <CardBody className="p-0">
        <ul className="divide-y divide-border-light">
          <li className="px-5 py-3 flex items-center gap-4">
            <span
              className="inline-flex items-center justify-center w-8 h-8 rounded-full text-white text-caption font-semibold shrink-0"
              style={{ backgroundColor: avatarBackground }}
            >
              {initialsFrom(name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-body text-navy font-medium truncate">{name}</p>
              {email && (
                <p className="text-caption text-slate truncate">{email}</p>
              )}
            </div>
            <StatusPill tone="action" className="shrink-0">
              {role}
            </StatusPill>
          </li>
        </ul>
        <SignerIdentityRow />
      </CardBody>
    </Card>
  );
}

/**
 * Your permanent signer identity (docs/attestation_esignature.md §2.5).
 *
 * Presented in monospace with a copy control, exactly like the BK-/OR- platform
 * IDs above, because it is the same kind of thing: an opaque, permanent
 * identifier. It is what every signature you ever record is attributed to, and
 * it survives your user row being deprovisioned — so it is the identifier an
 * attribution question years from now actually turns on. The same string appears
 * beneath the rendered signature block and stamped inside the signed PDF; §2.5
 * requires all three to agree.
 */
function SignerIdentityRow() {
  const { profile } = useUserProfile();
  // The API answers this probe with a refusal for anyone outside the analyst
  // ladder — correct, but it lands in the console as an error for a card that
  // has nothing to show. Ask only when the answer can be an identity.
  const identity = useMySignerIdentity(canHoldSignerIdentity(profile?.role));

  if (identity.isLoading) {
    return (
      <div className="px-5 py-3 border-t border-border-light">
        <SkeletonLine width="45%" />
      </div>
    );
  }
  // A viewer-only or service principal legitimately has no signer identity;
  // failing quietly is right here — this card is not the place to explain why.
  if (identity.error || !identity.data) return null;

  return (
    <div className="px-5 py-3 border-t border-border-light">
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <IdField
          label="Signer ID"
          value={identity.data.signerId}
          wide={false}
        />
        <div>
          <dt className="text-micro font-medium uppercase tracking-wider text-slate">
            Signing key
          </dt>
          <dd className="mt-1 flex items-center gap-2">
            <StatusPill tone={identity.data.hasActiveKey ? "success" : "amber"}>
              {identity.data.hasActiveKey ? "Enrolled" : "Not enrolled"}
            </StatusPill>
            <span className="text-caption text-slate">
              provisioned {fmtRelative(identity.data.provisionedAt)}
            </span>
          </dd>
        </div>
      </dl>
      {!identity.data.hasActiveKey && (
        <p className="mt-2 text-caption text-slate leading-relaxed">
          You hold a signer identity but no active signing key, so certification
          is refused rather than recorded unsigned. An administrator enrols the
          key.
        </p>
      )}
    </div>
  );
}
