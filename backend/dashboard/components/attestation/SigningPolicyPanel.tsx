"use client";

/**
 * Regulatory Reporting → Settings: signing policies
 * (docs/attestation_esignature.md §4.5).
 *
 * Who must sign which return is configuration, never code — which is the whole
 * reason it is editable here. Two Bank of Ghana facts are still unconfirmed
 * (whether a signed PDF artifact is required at all, and exactly which officers
 * must sign which of the thirteen returns), so the platform ships conservative
 * defaults and lets an administrator express the bank's real requirement without
 * a release.
 *
 * Two behaviours are deliberate rather than incidental:
 *
 * - **Org Owner-only**: this is the
 *   control that decides whether a filed return is properly attested.
 * - **Reason-required, versioned by effective date.** Saving does not edit a
 *   policy in place; it end-dates the identically scoped one and inserts a new
 *   row, so a return filed earlier can always be shown to have satisfied the
 *   rules that were actually in force at its reporting date.
 */

import { useState } from "react";
import { Loader2, Save, ShieldCheck } from "lucide-react";
import type {
  PolicyRead,
  SignatureSlotRead,
  SigningRole,
} from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import { FAMILY_LABELS } from "@/components/submissions/shared";
import {
  useReturnTemplates,
  useSigningPolicies,
  useUpsertSigningPolicy,
} from "@/lib/api/hooks";
import { useGrantAdministrationAccess } from "@/lib/api/grantAdministration";
import { fmtDateUTC, fmtTimestamp, isoDate } from "@/lib/api/values";
import { SIGNING_ROLE_LABELS, roleNoun } from "./shared";

/**
 * The roles a slot row may name. `board` is deliberately absent: it is the one
 * role whose availability depends on the return and on the installation, so it
 * gets a control of its own that can say why it is unavailable. Two controls
 * writing the same list would let a Board slot in through the dropdown while
 * the control beside it was explaining that it could not be added.
 */
const ROLE_OPTIONS: SigningRole[] = ["preparer", "approver", "witness"];
const BASIS_OPTIONS = ["solo", "consolidated"] as const;

/** Sentinel for "not scoped to this dimension" — the API takes null. */
const ANY = "";

interface SlotDraft {
  role: SigningRole;
  minCount: string;
  /** Comma-separated in the UI; empty means role-only (the conservative default). */
  officerTitles: string;
}

interface FormState {
  scopeBank: boolean;
  returnCode: string;
  returnFamily: string;
  basis: string;
  slots: SlotDraft[];
  requiredAttachments: string;
  requireSignature: boolean;
  requireSignedPdf: boolean;
  distinctSigners: boolean;
  orderedSlots: boolean;
  effectiveFrom: string;
  effectiveTo: string;
  reason: string;
}

/**
 * The one report whose attestation page this platform draws itself, and so the
 * only one that can carry a third signature block. Every other return's page
 * comes from the regulator's own workbook, where there is no third block to
 * sign — which is why the server refuses a Board signature on one rather than
 * inventing somewhere to put it.
 */
const BOARD_CAPABLE_FAMILY = "icaap";

/** The built-in maker-checker default, as the starting point for a new policy. */
function emptyForm(): FormState {
  return {
    scopeBank: true,
    returnCode: ANY,
    returnFamily: ANY,
    basis: ANY,
    slots: [
      { role: "preparer", minCount: "1", officerTitles: "" },
      { role: "approver", minCount: "1", officerTitles: "" },
    ],
    requiredAttachments: "",
    requireSignature: true,
    requireSignedPdf: false,
    distinctSigners: true,
    orderedSlots: false,
    effectiveFrom: isoDate(new Date()),
    effectiveTo: "",
    reason: "",
  };
}

export default function SigningPolicyPanel() {
  const canAdministerGrants = useGrantAdministrationAccess();
  // Non-owners see nothing rather than a disabled form: the API refuses the
  // write anyway, and a dead control invites support tickets.
  if (!canAdministerGrants) return null;
  return <SigningPolicyPanelInner />;
}

function SigningPolicyPanelInner() {
  const { bank } = useBankContext();
  const policiesQuery = useSigningPolicies();
  const templatesQuery = useReturnTemplates();
  const upsert = useUpsertSigningPolicy();

  const [form, setForm] = useState<FormState>(emptyForm);
  const [notice, setNotice] = useState<string | null>(null);

  const templates = templatesQuery.data?.templates ?? [];
  const policies = policiesQuery.data?.policies ?? [];

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setNotice(null);
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const setSlot = (index: number, patch: Partial<SlotDraft>) => {
    setNotice(null);
    setForm((prev) => ({
      ...prev,
      slots: prev.slots.map((slot, i) =>
        i === index ? { ...slot, ...patch } : slot,
      ),
    }));
  };

  const scopeMissing = form.returnCode === ANY && form.returnFamily === ANY;
  const reasonMissing = form.reason.trim().length === 0;
  const slotsMissing = form.slots.length === 0;

  /** Whether the deployment is currently suspending the signing it configures. */
  const signingSuspendedEverywhere =
    policiesQuery.data?.signingSuspendedDeploymentWide ?? false;
  const icaapSigningSuspended =
    policiesQuery.data?.icaapSigningSuspended ?? false;

  // A return code is the more specific scope, so it decides; otherwise the
  // family does. Same order the server resolves the artifact layout in.
  const scopedFamily =
    form.returnCode !== ANY
      ? (templates.find((template) => template.code === form.returnCode)
          ?.family ?? null)
      : form.returnFamily !== ANY
        ? form.returnFamily
        : null;
  const scopeCanCarryBoard = scopedFamily === BOARD_CAPABLE_FAMILY;
  const boardRequired = form.slots.some((slot) => slot.role === "board");
  // Not a preference once a third officer signs: each signature seals the ones
  // before it, so out-of-order signing invalidates an earlier signature. The
  // server refuses the policy without it; the form does not offer the refusal.
  const orderForced = boardRequired;
  const orderedSlots = form.orderedSlots || orderForced;

  const setBoardRequired = (required: boolean) => {
    setNotice(null);
    setForm((prev) => ({
      ...prev,
      slots: required
        ? [
            ...prev.slots.filter((slot) => slot.role !== "board"),
            { role: "board", minCount: "1", officerTitles: "" },
          ]
        : prev.slots.filter((slot) => slot.role !== "board"),
      orderedSlots: required ? true : prev.orderedSlots,
    }));
  };

  const handleSave = () => {
    const requiredSignatures: SignatureSlotRead[] = form.slots.map((slot) => ({
      role: slot.role,
      minCount: Math.max(Number.parseInt(slot.minCount, 10) || 1, 1),
      officerTitles: slot.officerTitles
        .split(",")
        .map((title) => title.trim())
        .filter(Boolean),
    }));
    upsert.mutate(
      {
        // A null bank scopes the policy to the whole organization.
        bankId: form.scopeBank ? (bank?.id ?? null) : null,
        returnCode: form.returnCode === ANY ? null : form.returnCode,
        returnFamily: form.returnFamily === ANY ? null : form.returnFamily,
        basis:
          form.basis === ANY
            ? null
            : (form.basis as (typeof BASIS_OPTIONS)[number]),
        requiredSignatures,
        requiredAttachments: form.requiredAttachments
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        requireSignature: form.requireSignature,
        requireSignedPdf: form.requireSignedPdf,
        distinctSigners: form.distinctSigners,
        orderedSlots,
        effectiveFrom: new Date(`${form.effectiveFrom}T00:00:00Z`),
        effectiveTo: form.effectiveTo || null,
        reason: form.reason.trim(),
      },
      {
        onSuccess: () => {
          setForm(emptyForm());
          setNotice(
            "Saved. The identically scoped open policy was end-dated; returns already filed keep the policy that was in force at their reporting date.",
          );
        },
      },
    );
  };

  const inputClass =
    "w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light";
  const labelClass = "block text-caption font-medium text-navy mb-1.5";

  return (
    <div className="space-y-6">
      <SectionCard
        title="Signing policies"
        subtitle="Who must sign which return — resolved most-specific-first, as at the reporting date"
        noPadding
      >
        {policiesQuery.isLoading ? (
          <div className="p-5">
            <SkeletonCard />
          </div>
        ) : policiesQuery.error ? (
          <div className="p-5">
            <ErrorPanel
              error={policiesQuery.error}
              onRetry={() => policiesQuery.refetch()}
              title="Could not load the signing policies"
            />
          </div>
        ) : (
          <>
            <SuspensionNotices
              everywhere={signingSuspendedEverywhere}
              icaap={icaapSigningSuspended}
            />
            {policies.length === 0 ? (
              <p className="px-5 py-4 text-caption text-slate leading-relaxed">
                No policies configured. Every return therefore falls back to the
                platform default: one preparer and one approver, who must be
                different people, each signing the exact figures being filed,
                and a signed PDF produced as part of the filing. No officer
                titles are enforced — a policy here is how you say that a named
                office, such as the Chief Financial Officer, must sign a
                particular return.
              </p>
            ) : (
              <ul>
                {policies.map((policy) => (
                  <PolicyRow key={policy.id} policy={policy} />
                ))}
              </ul>
            )}
          </>
        )}
      </SectionCard>

      <SectionCard
        title="Add or supersede a policy"
        subtitle="Reason-required and versioned by effective date — never edited in place"
        actions={
          <button
            type="button"
            disabled={
              upsert.isPending || scopeMissing || reasonMissing || slotsMissing
            }
            onClick={handleSave}
            title={
              scopeMissing
                ? "Scope the policy to a return code or a return family."
                : reasonMissing
                  ? "A reason is required."
                  : slotsMissing
                    ? "Add at least one required signature."
                    : undefined
            }
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            {upsert.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <Save size={13} aria-hidden />
            )}
            Save policy
          </button>
        }
      >
        <div className="space-y-5 max-w-3xl">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={labelClass} htmlFor="policy-return-code">
                Return code
              </label>
              <select
                id="policy-return-code"
                value={form.returnCode}
                onChange={(event) => set("returnCode", event.target.value)}
                className={inputClass}
              >
                <option value={ANY}>Any (scope by family instead)</option>
                {templates.map((template) => (
                  <option key={template.code} value={template.code}>
                    {template.code} — {template.title}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass} htmlFor="policy-return-family">
                Return family
              </label>
              <select
                id="policy-return-family"
                value={form.returnFamily}
                onChange={(event) => set("returnFamily", event.target.value)}
                className={inputClass}
              >
                <option value={ANY}>Any (scope by return code instead)</option>
                {Object.entries(FAMILY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass} htmlFor="policy-basis">
                Reporting basis
              </label>
              <select
                id="policy-basis"
                value={form.basis}
                onChange={(event) => set("basis", event.target.value)}
                className={inputClass}
              >
                <option value={ANY}>Any basis</option>
                {BASIS_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value === "solo" ? "Solo" : "Consolidated"}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <span className={labelClass}>Applies to</span>
              <div
                role="radiogroup"
                aria-label="Policy scope"
                className="flex items-center gap-2"
              >
                <ScopeToggle
                  selected={form.scopeBank}
                  label={bank ? `This bank (${bank.id})` : "This bank"}
                  onSelect={() => set("scopeBank", true)}
                />
                <ScopeToggle
                  selected={!form.scopeBank}
                  label="All banks in the organization"
                  onSelect={() => set("scopeBank", false)}
                />
              </div>
            </div>
          </div>

          {scopeMissing && (
            <p className="text-caption text-warning">
              A policy must scope to a return code or a return family —
              otherwise there is nothing for the resolver to match.
            </p>
          )}

          {/* Required signatures */}
          <div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-caption font-medium text-navy">
                Required signatures
              </p>
              <button
                type="button"
                onClick={() =>
                  set("slots", [
                    ...form.slots,
                    { role: "approver", minCount: "1", officerTitles: "" },
                  ])
                }
                className="inline-flex items-center px-2.5 py-1 text-micro font-medium text-navy border border-border rounded hover:bg-surface"
              >
                Add slot
              </button>
            </div>
            <ul className="mt-2 space-y-2">
              {form.slots.map((slot, index) => (
                <li
                  key={`${slot.role}-${index}`}
                  className="grid grid-cols-1 sm:grid-cols-[9rem_5rem_1fr_auto] gap-2 items-end rounded border border-border-light bg-surface px-3 py-2.5"
                >
                  <label className="block">
                    <span className="block text-micro font-medium uppercase tracking-wider text-slate mb-1">
                      Role
                    </span>
                    <select
                      aria-label={`Role for slot ${index + 1}`}
                      value={slot.role}
                      onChange={(event) =>
                        setSlot(index, {
                          role: event.target.value as SigningRole,
                        })
                      }
                      className="w-full rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
                    >
                      {(slot.role === "board"
                        ? [...ROLE_OPTIONS, "board" as SigningRole]
                        : ROLE_OPTIONS
                      ).map((role) => (
                        <option key={role} value={role}>
                          {SIGNING_ROLE_LABELS[role]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="block text-micro font-medium uppercase tracking-wider text-slate mb-1">
                      Count
                    </span>
                    <input
                      type="number"
                      min={1}
                      aria-label={`Minimum count for slot ${index + 1}`}
                      value={slot.minCount}
                      onChange={(event) =>
                        setSlot(index, { minCount: event.target.value })
                      }
                      className="w-full rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy tnum"
                    />
                  </label>
                  <label className="block">
                    <span className="block text-micro font-medium uppercase tracking-wider text-slate mb-1">
                      Officer titles (comma-separated; blank = role only)
                    </span>
                    <input
                      type="text"
                      aria-label={`Officer titles for slot ${index + 1}`}
                      placeholder="Chief Financial Officer, Head of Finance"
                      value={slot.officerTitles}
                      onChange={(event) =>
                        setSlot(index, { officerTitles: event.target.value })
                      }
                      className="w-full rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      set(
                        "slots",
                        form.slots.filter((_, i) => i !== index),
                      )
                    }
                    className="inline-flex items-center px-2.5 py-1.5 text-micro font-medium text-critical border border-critical/30 rounded hover:bg-critical-light/40"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-micro text-slate leading-relaxed">
              Officer titles are matched against the signer&apos;s recorded job
              title, so &quot;the CFO must sign&quot; becomes enforceable rather
              than aspirational. Leave blank until the requirement is confirmed
              with the regulator — enforcing a guessed title would block a
              legitimate signer.
            </p>
          </div>

          {/* The Board signature. Offered only where a third block can exist,
              and only while this installation is signing the report at all. */}
          {scopeCanCarryBoard && (
            <BoardSignatureControl
              required={boardRequired}
              suspended={icaapSigningSuspended}
              onChange={setBoardRequired}
            />
          )}

          {/* Flags */}
          <div className="space-y-2">
            <Toggle
              checked={form.requireSignature}
              onChange={(value) => set("requireSignature", value)}
              label="A signature is required for returns in this scope"
              hint="The platform requires one for every return. Turn this off to exempt a scope deliberately — the daily return, for instance, where a two-person ceremony every weekday morning may be infeasible."
            />
            <Toggle
              checked={form.requireSignedPdf}
              onChange={(value) => set("requireSignedPdf", value)}
              label="A cryptographically signed PDF artifact is required"
              hint="The platform requires one for every return. Leaving this off relaxes that for this scope, to the detached attestation alone — a signed PDF is what makes signature validity and tamper analysis part of verification."
            />
            <Toggle
              checked={form.distinctSigners}
              onChange={(value) => set("distinctSigners", value)}
              label="Every required signature must come from a different person"
              hint="Segregation of duties. Off only where a scope genuinely has one eligible officer."
            />
            <Toggle
              checked={orderedSlots}
              disabled={orderForced}
              onChange={(value) => set("orderedSlots", value)}
              label="Signatures must be given in order"
              hint={
                orderForced
                  ? "Always on once the Board signs: each signature seals the ones given before it, so a Board signature given out of turn would invalidate the approver's."
                  : "The preparer signs first, then the approver. Each signature seals the ones given before it, so the order is part of the document rather than a matter of etiquette."
              }
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className={labelClass} htmlFor="policy-effective-from">
                Effective from
              </label>
              <input
                id="policy-effective-from"
                type="date"
                value={form.effectiveFrom}
                onChange={(event) => set("effectiveFrom", event.target.value)}
                className={`${inputClass} tnum`}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="policy-effective-to">
                Effective to{" "}
                <span className="font-normal text-slate">
                  (blank = open-ended)
                </span>
              </label>
              <input
                id="policy-effective-to"
                type="date"
                value={form.effectiveTo}
                onChange={(event) => set("effectiveTo", event.target.value)}
                className={`${inputClass} tnum`}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="policy-attachments">
                Required attachments
              </label>
              <input
                id="policy-attachments"
                type="text"
                placeholder="board_resolution, senior_management_report"
                value={form.requiredAttachments}
                onChange={(event) =>
                  set("requiredAttachments", event.target.value)
                }
                className={inputClass}
              />
            </div>
          </div>

          <div>
            <label className={labelClass} htmlFor="policy-reason">
              Reason <span className="font-normal text-slate">(required)</span>
            </label>
            <textarea
              id="policy-reason"
              rows={2}
              value={form.reason}
              onChange={(event) => set("reason", event.target.value)}
              placeholder="e.g. Board resolution of 12 Jul 2026: the CFO signs BSD5A and the Head of Finance signs BSD2."
              className={inputClass}
            />
          </div>

          {notice && (
            <p
              role="status"
              className="text-caption text-success leading-relaxed"
            >
              <ShieldCheck size={12} className="inline mr-1" aria-hidden />
              {notice}
            </p>
          )}
          {upsert.error && (
            <ErrorPanel
              error={upsert.error}
              title="Could not save the policy"
            />
          )}
        </div>
      </SectionCard>
    </div>
  );
}

function ScopeToggle({
  selected,
  label,
  onSelect,
}: {
  selected: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={`px-3 py-2 rounded text-caption font-medium border transition-colors ${
        selected
          ? "border-action/30 bg-action-light text-action"
          : "border-border text-slate hover:text-navy"
      }`}
    >
      {label}
    </button>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex items-start gap-2.5 py-1 select-none ${
        disabled ? "cursor-not-allowed" : "cursor-pointer"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 accent-teal"
      />
      <span className={`text-body ${disabled ? "text-slate" : "text-navy"}`}>
        {label}
        <span className="block text-caption text-slate leading-relaxed">
          {hint}
        </span>
      </span>
    </label>
  );
}

/**
 * What this installation is doing to the policies below, in the same card that
 * lists them.
 *
 * A settings screen that showed the rows alone would state a requirement the
 * platform is not applying — the two deployment switches outrank every row, and
 * an administrator reading "one preparer, one approver, Board" has no way to
 * know from the row that nobody is being asked to sign anything. Neither switch
 * deletes a policy: each one is stated as suspension, because that is what it
 * is, and everything written here is applied again the moment it is lifted.
 */
function SuspensionNotices({
  everywhere,
  icaap,
}: {
  everywhere: boolean;
  icaap: boolean;
}) {
  if (!everywhere && !icaap) return null;
  return (
    <div className="px-5 pt-4 space-y-2">
      {everywhere && (
        <p
          role="status"
          data-testid="signing-suspended-deployment-wide"
          className="rounded border border-warning/30 bg-warning-light/40 px-3.5 py-2.5 text-caption text-navy/85 leading-relaxed"
        >
          <strong className="font-medium text-navy">
            No return currently requires a signature.
          </strong>{" "}
          Signing is switched off for this installation, so every policy below
          is recorded but not applied and each return takes the ordinary
          prepare-and-approve route. Nothing is lost: the policies take effect
          again as soon as your AequorOS administrator switches signing back on.
        </p>
      )}
      {icaap && (
        <p
          role="status"
          data-testid="icaap-signing-suspended"
          className="rounded border border-border bg-surface px-3.5 py-2.5 text-caption text-navy/85 leading-relaxed"
        >
          <strong className="font-medium text-navy">
            The ICAAP assessment is not signed in this installation.
          </strong>{" "}
          An ICAAP policy below is recorded but not applied, and a Board
          signature cannot be required, so the assessment is prepared and
          approved in the usual way and the Board resolution is filed with it as
          the evidence of the Board&apos;s approval. Ask your AequorOS
          administrator to switch ICAAP signing on if your Board wants to sign
          the report itself.
        </p>
      )}
    </div>
  );
}

/**
 * The Board slot: what it means, and why it is off.
 *
 * Shown only for the ICAAP assessment, which is the only report with a third
 * signature block to fill. Refusing rather than hiding it while the
 * installation has ICAAP signing switched off is deliberate: a control that
 * silently did nothing would be worse than one that says why it cannot.
 */
function BoardSignatureControl({
  required,
  suspended,
  onChange,
}: {
  required: boolean;
  suspended: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div
      data-testid="board-signature-control"
      className="rounded border border-border-light bg-surface px-3.5 py-3"
    >
      <p className="text-caption font-medium text-navy">Board signature</p>
      <div className="mt-1.5">
        <Toggle
          checked={required && !suspended}
          disabled={suspended}
          onChange={onChange}
          label="A member of the Board must sign the assessment"
          hint={
            suspended
              ? "Unavailable: the ICAAP assessment is not signed in this installation. Ask your AequorOS administrator to switch ICAAP signing on first."
              : "Adds a third signature block to the assessment, signed after the approver. The Board resolution is still filed with the report either way — turning this on does not replace it, and turning it off never relaxes it."
          }
        />
      </div>
      {!suspended && (
        <p className="mt-1 text-micro text-slate leading-relaxed">
          The Bank of Ghana&apos;s ICAAP directive asks for the Board&apos;s
          challenge, its approval and its resolution — not for a Board member to
          sign the report itself. This is here for a Board that wants to sign
          regardless.
        </p>
      )}
    </div>
  );
}

/** One configured policy, with its scope, slots, flags, and effective window. */
function PolicyRow({ policy }: { policy: PolicyRead }) {
  const scope = [
    policy.bankId ?? "All banks",
    policy.returnCode ??
      (policy.returnFamily
        ? (FAMILY_LABELS[policy.returnFamily] ?? policy.returnFamily)
        : "Any return"),
    policy.basis ?? "any basis",
  ].join(" · ");

  return (
    <li className="px-5 py-3.5 border-b border-border-light last:border-b-0 space-y-1.5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <p className="text-body font-medium text-navy">{scope}</p>
        <div className="flex items-center gap-1.5 flex-wrap">
          {!policy.requireSignature && (
            <StatusPill tone="slate">Signature not required</StatusPill>
          )}
          {policy.requireSignedPdf && (
            <StatusPill tone="amber">Signed PDF</StatusPill>
          )}
          {policy.orderedSlots && (
            <StatusPill tone="slate">Signed in order</StatusPill>
          )}
          {!policy.distinctSigners && (
            <StatusPill tone="amber">Distinct signers off</StatusPill>
          )}
          {policy.effectiveTo ? (
            <StatusPill tone="slate">Ended</StatusPill>
          ) : (
            <StatusPill tone="success">In force</StatusPill>
          )}
        </div>
      </div>

      <p className="text-caption text-navy/85">
        {policy.requiredSignatures.length === 0
          ? "No slots — the platform default applies."
          : policy.requiredSignatures
              .map(
                (slot) =>
                  `${roleNoun(slot.role)} ×${slot.minCount}` +
                  (slot.officerTitles.length > 0
                    ? ` (${slot.officerTitles.join(" or ")})`
                    : ""),
              )
              .join(" + ")}
      </p>

      {policy.requiredAttachments.length > 0 && (
        <p className="text-caption text-navy/85">
          Attachments: {policy.requiredAttachments.join(", ")}
        </p>
      )}

      <p className="font-mono text-micro text-slate tnum">
        {fmtDateUTC(policy.effectiveFrom)} →{" "}
        {policy.effectiveTo ? fmtDateUTC(new Date(policy.effectiveTo)) : "open"}{" "}
        · updated {fmtTimestamp(policy.updatedAt)}
      </p>
      <p className="text-caption text-slate leading-relaxed">
        Reason: {policy.reason}
      </p>
    </li>
  );
}
