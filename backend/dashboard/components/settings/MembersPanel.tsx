"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Clock3,
  KeyRound,
  Plus,
  ShieldCheck,
  X,
} from "lucide-react";
import type {
  BindingCreateRequest,
  BindingCreateResponse,
  BindingRead,
  MemberRead,
} from "@aequoros/risk-service-api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { SkeletonLine } from "@/components/ui/Skeleton";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { useBanks } from "@/lib/api/hooks";
import { authApi, authorizationApi, normalizeApiError } from "@/lib/api/client";
import { avatarColor, initialsFrom } from "@/lib/api/identity";
import { fmtRelative } from "@/lib/api/values";
import {
  canAddGrantToMember,
  grantAuthoritySentence,
  MODULE_OPTIONS,
  ROLE_OPTIONS,
  SENSITIVITY_OPTIONS,
  visibleGrantFragments,
  type GrantDraft,
} from "@/lib/api/grants";

const MEMBERS_KEY = ["settings", "organization-members"];
const REQUESTS_KEY = ["settings", "sso-access-requests"];

function memberName(member: MemberRead): string {
  return member.displayName || member.email;
}

function lifecycleTone(member: MemberRead): StatusTone {
  if (member.lifecycleStatus === "active") return "success";
  return "slate";
}

function lifecycleLabel(member: MemberRead): string {
  if (member.lifecycleStatus === "invited") return "Invited";
  if (member.lifecycleStatus === "deactivated") return "Deactivated";
  return "Active";
}

function noAccessLabel(member: MemberRead): string | null {
  if (member.accessRequestState === "approval_needed") {
    return "SSO approval needed · no access yet";
  }
  if (member.lifecycleStatus === "invited")
    return "Invitation pending · no access yet";
  if (member.lifecycleStatus === "deactivated")
    return "Deactivated · no access";
  if (member.activeGrantCount === 0) return "No access grants";
  return null;
}

function formatMethod(method: MemberRead["authenticationMethod"]): string {
  if (method === "sso") return "Single sign-on";
  if (method === "service") return "Service credential";
  return "Password";
}

function grantorLabel(grant: BindingRead): string {
  if (grant.grantedByType === "system")
    return `${grant.grantedByName} · System`;
  if (grant.grantedByType === "operator")
    return `${grant.grantedByName} · AequorOS operator`;
  return `${grant.grantedByName} · Organization member`;
}

export default function MembersPanel() {
  const queryClient = useQueryClient();
  const membersQuery = useQuery({
    queryKey: MEMBERS_KEY,
    queryFn: () => authorizationApi.listOrganizationMembers(),
    retry: false,
  });
  const banksQuery = useBanks();
  const [selected, setSelected] = useState<MemberRead | null>(null);
  const [granting, setGranting] = useState<MemberRead | null>(null);
  const [revoking, setRevoking] = useState<BindingRead | null>(null);

  const members = membersQuery.data?.members ?? [];
  const currentSelected = selected
    ? (members.find((member) => member.userId === selected.userId) ?? selected)
    : null;

  if (membersQuery.error) return null;

  return (
    <Card className="lg:col-span-2">
      <div id="members" className="scroll-mt-24" />
      <CardHeader
        title="Members"
        action={
          membersQuery.data ? (
            <span className="text-caption text-slate">
              {members.length} {members.length === 1 ? "member" : "members"}
            </span>
          ) : undefined
        }
      />
      <CardBody className="p-0">
        {membersQuery.isLoading ? (
          <div className="space-y-4 p-5">
            <SkeletonLine width="42%" />
            <SkeletonLine width="76%" />
            <SkeletonLine width="61%" />
          </div>
        ) : members.length === 0 ? (
          <p className="p-5 text-body text-slate">
            No organization members yet.
          </p>
        ) : (
          <ul className="divide-y divide-border-light">
            {members.map((member) => (
              <MemberRow
                key={member.userId}
                member={member}
                onOpen={() => setSelected(member)}
                onGrant={() => setGranting(member)}
              />
            ))}
          </ul>
        )}
      </CardBody>

      {currentSelected && (
        <MemberDetail
          member={currentSelected}
          onClose={() => setSelected(null)}
          onGrant={() => {
            setSelected(null);
            setGranting(currentSelected);
          }}
          onRevoke={(grant) => {
            setSelected(null);
            setRevoking(grant);
          }}
        />
      )}
      {granting && (
        <GrantComposer
          member={granting}
          banks={banksQuery.data?.banks ?? []}
          onClose={() => setGranting(null)}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: MEMBERS_KEY });
            void queryClient.invalidateQueries({ queryKey: REQUESTS_KEY });
          }}
        />
      )}
      {revoking && (
        <RevokeDialog
          grant={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={() => {
            setRevoking(null);
            void queryClient.invalidateQueries({ queryKey: MEMBERS_KEY });
          }}
        />
      )}
    </Card>
  );
}

function MemberRow({
  member,
  onOpen,
  onGrant,
}: {
  member: MemberRead;
  onOpen: () => void;
  onGrant: () => void;
}) {
  const name = memberName(member);
  const authority = visibleGrantFragments(member.grants);
  const noAccess = noAccessLabel(member);
  return (
    <li className="px-5 py-4 flex flex-col gap-3 sm:flex-row sm:items-center">
      <button
        type="button"
        onClick={onOpen}
        className="min-w-0 flex flex-1 items-center gap-3 text-left"
        aria-label={`View ${name}`}
      >
        <span
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-caption font-semibold text-white"
          style={{ backgroundColor: avatarColor(member.userId) }}
        >
          {initialsFrom(name)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate text-body font-medium text-navy">
              {name}
            </span>
            <StatusPill tone={lifecycleTone(member)}>
              {lifecycleLabel(member)}
            </StatusPill>
          </span>
          <span className="block truncate text-caption text-slate">
            {member.email}
          </span>
          {noAccess ? (
            <span className="mt-1 block text-caption font-medium text-slate">
              {noAccess}
            </span>
          ) : (
            <span className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-caption text-slate">
              <strong className="font-medium text-navy">
                {member.activeGrantCount}{" "}
                {member.activeGrantCount === 1 ? "grant" : "grants"}
              </strong>
              {authority.fragments.map((fragment) => (
                <span key={fragment} className="truncate">
                  · {fragment}
                </span>
              ))}
              {authority.remaining > 0 && (
                <span>+{authority.remaining} more</span>
              )}
            </span>
          )}
        </span>
        <ChevronRight className="shrink-0 text-slate" size={17} aria-hidden />
      </button>
      <button
        type="button"
        onClick={onGrant}
        disabled={!canAddGrantToMember(member)}
        className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Plus size={14} aria-hidden />
        {member.accessRequestState === "approval_needed"
          ? "Complete access"
          : "Add grant"}
      </button>
    </li>
  );
}

function DialogFrame({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    headingRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="members-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy/45 p-4 backdrop-blur-sm"
    >
      <div
        className={`max-h-[90vh] w-full overflow-y-auto rounded-lg border border-border bg-surface-raised shadow-overlay ${wide ? "max-w-3xl" : "max-w-xl"}`}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-border-light bg-surface-raised px-5 py-4">
          <h2
            id="members-dialog-title"
            ref={headingRef}
            tabIndex={-1}
            className="text-h3 text-navy outline-none"
          >
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1.5 text-slate hover:bg-surface-muted hover:text-navy"
            aria-label="Close"
          >
            <X size={18} aria-hidden />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function MemberDetail({
  member,
  onClose,
  onGrant,
  onRevoke,
}: {
  member: MemberRead;
  onClose: () => void;
  onGrant: () => void;
  onRevoke: (grant: BindingRead) => void;
}) {
  const name = memberName(member);
  return (
    <DialogFrame title={name} onClose={onClose} wide>
      <div className="space-y-5 p-5">
        <div className="grid grid-cols-2 gap-4 rounded-md border border-border-light bg-surface p-4 text-caption sm:grid-cols-4">
          <DetailFact label="Status" value={lifecycleLabel(member)} />
          <DetailFact
            label="Authentication"
            value={formatMethod(member.authenticationMethod)}
          />
          <DetailFact
            label="Last activity"
            value={
              member.lastActivityAt
                ? fmtRelative(member.lastActivityAt)
                : "Never"
            }
          />
          <DetailFact
            label="Active access"
            value={`${member.activeGrantCount} ${member.activeGrantCount === 1 ? "grant" : "grants"}`}
          />
        </div>
        {noAccessLabel(member) && (
          <div className="rounded-md border border-warning/25 bg-warning-light/50 px-4 py-3 text-body text-navy">
            {noAccessLabel(member)}
          </div>
        )}
        <div className="flex items-center justify-between gap-4">
          <h3 className="text-body font-medium text-navy">Grant history</h3>
          <button
            type="button"
            onClick={onGrant}
            disabled={!canAddGrantToMember(member)}
            className="inline-flex items-center gap-1.5 px-3 py-2 btn-primary text-caption font-medium disabled:opacity-50"
          >
            <Plus size={14} aria-hidden /> Add grant
          </button>
        </div>
        {member.grants.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-5 text-center text-body text-slate">
            No grants have been recorded for this member.
          </p>
        ) : (
          <ul className="space-y-3">
            {member.grants.map((grant) => (
              <li
                key={grant.id}
                className="rounded-md border border-border-light p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-body font-medium leading-relaxed text-navy">
                    {grant.authoritySentence}
                  </p>
                  <StatusPill tone={grant.effective ? "success" : "slate"}>
                    {grant.effective ? "Active" : grant.status}
                  </StatusPill>
                </div>
                <dl className="mt-3 grid gap-3 text-caption text-slate sm:grid-cols-2">
                  <DetailFact label="Granted by" value={grantorLabel(grant)} />
                  <DetailFact
                    label="Granted"
                    value={grant.grantedAt.toLocaleString()}
                  />
                  <DetailFact
                    label="Valid from"
                    value={grant.validFrom.toLocaleString()}
                  />
                  <DetailFact
                    label="Expires"
                    value={
                      grant.validUntil
                        ? grant.validUntil.toLocaleString()
                        : "No expiry"
                    }
                  />
                  <DetailFact label="Reason" value={grant.grantReason} />
                  <DetailFact
                    label="Permissions"
                    value={grant.effectivePermissions.join(", ")}
                  />
                  {grant.revokedAt && (
                    <DetailFact
                      label="Revoked"
                      value={grant.revokedAt.toLocaleString()}
                    />
                  )}
                  {grant.revokedByName && (
                    <DetailFact
                      label="Revoked by"
                      value={grant.revokedByName}
                    />
                  )}
                  {grant.revokedReason && (
                    <DetailFact
                      label="Revocation reason"
                      value={grant.revokedReason}
                    />
                  )}
                </dl>
                {grant.effective && grant.roleBundle !== "org_owner" && (
                  <button
                    type="button"
                    onClick={() => onRevoke(grant)}
                    className="mt-4 text-caption font-medium text-danger hover:underline"
                  >
                    Revoke this access
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </DialogFrame>
  );
}

function DetailFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-1 break-words text-caption text-navy">{value}</dd>
    </div>
  );
}

function initialDraft(
  banks: readonly { id: string; name: string }[],
): GrantDraft {
  const bank = banks[0];
  return {
    roleBundle: "analyst",
    institutionScope: bank ? "institution" : "organization",
    institutionId: bank?.id,
    institutionName: bank?.name ?? "every institution in this organization",
    moduleScope: "liq",
    sensitivityScope: "confidential",
    reason: "",
  };
}

function GrantComposer({
  member,
  banks,
  onClose,
  onSaved,
}: {
  member: MemberRead;
  banks: readonly { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState<"define" | "review" | "done">("define");
  const [draft, setDraft] = useState<GrantDraft>(() => initialDraft(banks));
  const [saved, setSaved] = useState<BindingCreateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = memberName(member);
  const sentence = grantAuthoritySentence(name, draft);
  const isPendingApproval = member.accessRequestState === "approval_needed";

  const submit = useMutation({
    mutationFn: async () => {
      const scope = {
        roleBundle: draft.roleBundle,
        institutionScope: draft.institutionScope,
        institutionId:
          draft.institutionScope === "institution"
            ? draft.institutionId
            : undefined,
        moduleScope: draft.moduleScope,
        sensitivityScope: draft.sensitivityScope,
        reason: draft.reason.trim(),
      };
      if (isPendingApproval) {
        return authApi.authApproveSsoAccessRequest({
          userId: member.userId,
          ssoAccessRequestApprove: scope,
        });
      }
      return authorizationApi.createAuthorizationBinding({
        bindingCreateRequest: {
          ...scope,
          principalUserId: member.userId,
        } satisfies BindingCreateRequest,
      });
    },
    onSuccess: (result) => {
      setSaved(result);
      setStep("done");
      onSaved();
    },
    onError: async (failure) =>
      setError((await normalizeApiError(failure)).message),
  });

  const updateRole = (roleBundle: GrantDraft["roleBundle"]) => {
    setError(null);
    if (roleBundle === "account_admin") {
      setDraft({
        ...draft,
        roleBundle,
        institutionScope: "organization",
        institutionId: undefined,
        institutionName: "every institution in this organization",
        moduleScope: "account",
        sensitivityScope: "all",
      });
      return;
    }
    setDraft({ ...draft, roleBundle });
  };

  const resetForAnother = () => {
    setDraft(initialDraft(banks));
    setSaved(null);
    setError(null);
    setStep("define");
  };

  return (
    <DialogFrame
      title={
        isPendingApproval
          ? `Complete access for ${name}`
          : `Add grant for ${name}`
      }
      onClose={onClose}
      wide
    >
      <div className="border-b border-border-light px-5 py-3">
        <ol
          className="flex items-center gap-2 text-caption font-medium"
          aria-label="Grant steps"
        >
          {(["Define", "Review", "Done"] as const).map((label, index) => {
            const current = ["define", "review", "done"].indexOf(step);
            return (
              <li
                key={label}
                className={`flex items-center gap-2 ${index <= current ? "text-action" : "text-slate"}`}
              >
                <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-current text-micro">
                  {index < current ? (
                    <Check size={12} aria-hidden />
                  ) : (
                    index + 1
                  )}
                </span>
                {label}
                {index < 2 && (
                  <ChevronRight size={14} className="text-slate" aria-hidden />
                )}
              </li>
            );
          })}
        </ol>
      </div>

      {step === "define" && (
        <form
          className="space-y-5 p-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (draft.reason.trim()) setStep("review");
          }}
        >
          {isPendingApproval && (
            <div className="rounded-md border border-warning/25 bg-warning-light/50 px-4 py-3 text-caption leading-relaxed text-navy">
              Single sign-on verified this identity, but it has no access.
              Approval creates the one scoped grant defined below.
            </div>
          )}
          <p className="text-caption text-slate">
            Member <strong className="font-medium text-navy">{name}</strong> is
            fixed for this grant.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <GrantSelect
              label="Role bundle"
              value={draft.roleBundle}
              options={ROLE_OPTIONS}
              onChange={(value) =>
                updateRole(value as GrantDraft["roleBundle"])
              }
            />
            <GrantSelect
              label="Institution coverage"
              value={
                draft.institutionScope === "organization"
                  ? "organization"
                  : (draft.institutionId ?? "")
              }
              disabled={draft.roleBundle === "account_admin"}
              options={[
                ["organization", "Every institution in the organization"],
                ...banks.map((bank) => [bank.id, bank.name] as const),
              ]}
              onChange={(value) => {
                const bank = banks.find((candidate) => candidate.id === value);
                setDraft({
                  ...draft,
                  institutionScope: bank ? "institution" : "organization",
                  institutionId: bank?.id,
                  institutionName:
                    bank?.name ?? "every institution in this organization",
                });
              }}
            />
            <GrantSelect
              label="Module"
              value={draft.moduleScope}
              disabled={draft.roleBundle === "account_admin"}
              options={MODULE_OPTIONS}
              onChange={(value) =>
                setDraft({
                  ...draft,
                  moduleScope: value as GrantDraft["moduleScope"],
                })
              }
            />
            <GrantSelect
              label="Sensitivity"
              value={draft.sensitivityScope}
              disabled={draft.roleBundle === "account_admin"}
              options={SENSITIVITY_OPTIONS}
              onChange={(value) =>
                setDraft({
                  ...draft,
                  sensitivityScope: value as GrantDraft["sensitivityScope"],
                })
              }
            />
          </div>
          <label className="block">
            <span className="mb-1.5 block text-caption font-medium text-navy">
              Reason
            </span>
            <textarea
              required
              minLength={3}
              rows={3}
              value={draft.reason}
              onChange={(event) =>
                setDraft({ ...draft, reason: event.target.value })
              }
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
              placeholder="Why this authority is required"
            />
          </label>
          <SentencePreview sentence={sentence} />
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-4 py-2.5 text-body font-medium text-navy hover:bg-surface-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!draft.reason.trim()}
              className="px-4 py-2.5 btn-primary text-body font-medium disabled:opacity-50"
            >
              Review grant
            </button>
          </div>
        </form>
      )}

      {step === "review" && (
        <div className="space-y-5 p-5">
          <p className="text-body text-slate">
            Review the exact authority before granting it.
          </p>
          <SentencePreview sentence={sentence} />
          <div className="rounded-md border border-border-light p-4">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Reason
            </p>
            <p className="mt-1 text-body text-navy">{draft.reason}</p>
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-md bg-critical-light px-4 py-3 text-caption text-critical"
            >
              {error}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={() => setStep("define")}
              className="rounded-md border border-border px-4 py-2.5 text-body font-medium text-navy hover:bg-surface-muted"
            >
              Back
            </button>
            <button
              type="button"
              onClick={() => submit.mutate()}
              disabled={submit.isPending}
              className="inline-flex items-center gap-2 px-4 py-2.5 btn-primary text-body font-medium disabled:opacity-50"
            >
              <ShieldCheck size={15} aria-hidden />{" "}
              {submit.isPending
                ? "Granting…"
                : isPendingApproval
                  ? "Approve and grant"
                  : "Grant access"}
            </button>
          </div>
        </div>
      )}

      {step === "done" && saved && (
        <div className="space-y-5 p-5">
          <div className="flex items-center gap-3 text-success">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-success-light">
              <Check size={19} aria-hidden />
            </span>
            <p className="text-h3 text-navy">Grant created</p>
          </div>
          <SentencePreview sentence={saved.binding.authoritySentence} />
          {saved.sodDecision.outcome === "warn" && (
            <div className="rounded-md border border-warning/25 bg-warning-light/50 px-4 py-3 text-caption text-navy">
              {saved.sodDecision.findings
                .map((finding) => finding.message)
                .join(" ")}
            </div>
          )}
          <div className="flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-4 py-2.5 text-body font-medium text-navy hover:bg-surface-muted"
            >
              Done
            </button>
            {!isPendingApproval && (
              <button
                type="button"
                onClick={resetForAnother}
                className="inline-flex items-center gap-2 px-4 py-2.5 btn-primary text-body font-medium"
              >
                <Plus size={15} aria-hidden /> Add another grant
              </button>
            )}
          </div>
        </div>
      )}
    </DialogFrame>
  );
}

function GrantSelect({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-caption font-medium text-navy">
        {label}
      </span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-md border border-border bg-surface px-3 py-2.5 text-body text-navy disabled:opacity-65"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
}

function SentencePreview({ sentence }: { sentence: string }) {
  return (
    <blockquote className="rounded-md border-l-4 border-action bg-action-light/45 px-4 py-3 text-body font-medium leading-relaxed text-navy">
      {sentence}
    </blockquote>
  );
}

function RevokeDialog({
  grant,
  onClose,
  onRevoked,
}: {
  grant: BindingRead;
  onClose: () => void;
  onRevoked: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: () =>
      authorizationApi.revokeAuthorizationBinding({
        bindingId: grant.id,
        bindingRevokeRequest: { reason: reason.trim() },
      }),
    onSuccess: onRevoked,
    onError: async (failure) =>
      setError((await normalizeApiError(failure)).message),
  });
  return (
    <DialogFrame title="Revoke access" onClose={onClose}>
      <form
        className="space-y-4 p-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (reason.trim()) revoke.mutate();
        }}
      >
        <SentencePreview sentence={grant.authoritySentence} />
        <div className="rounded-md border border-warning/25 bg-warning-light/50 px-4 py-3 text-caption leading-relaxed text-navy">
          <p className="flex items-start gap-2">
            <KeyRound
              className="mt-0.5 shrink-0 text-warning"
              size={16}
              aria-hidden
            />{" "}
            This person loses this access immediately. Their current AequorOS
            sign-ins end and they will be asked to sign in again. Their other
            grants stay active.
          </p>
        </div>
        <label className="block">
          <span className="mb-1.5 block text-caption font-medium text-navy">
            Reason
          </span>
          <textarea
            required
            minLength={3}
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
            placeholder="Why this access is being revoked"
          />
        </label>
        {error && (
          <p role="alert" className="text-caption text-critical">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-4 py-2.5 text-body font-medium text-navy hover:bg-surface-muted"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!reason.trim() || revoke.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-danger px-4 py-2.5 text-body font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            <Clock3 size={15} aria-hidden />{" "}
            {revoke.isPending ? "Revoking…" : "Revoke access"}
          </button>
        </div>
      </form>
    </DialogFrame>
  );
}
