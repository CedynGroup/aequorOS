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

const ROLE_OPTIONS: SigningRole[] = [
  "preparer",
  "approver",
  "board",
  "witness",
];
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
  effectiveFrom: string;
  effectiveTo: string;
  reason: string;
}

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
        ) : policies.length === 0 ? (
          <p className="px-5 py-4 text-caption text-slate leading-relaxed">
            No policies configured. Every return therefore falls back to the
            platform default — one preparer plus one approver, distinct signers,
            role-only (no officer titles enforced), and no signed PDF required.
            The daily returns family ships exempt from signing entirely, because
            a two-person ceremony every weekday morning is operationally
            infeasible and imposing one on an assumption would cause missed
            statutory deadlines or shared credentials.
          </p>
        ) : (
          <ul>
            {policies.map((policy) => (
              <PolicyRow key={policy.id} policy={policy} />
            ))}
          </ul>
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
                      {ROLE_OPTIONS.map((role) => (
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

          {/* Flags */}
          <div className="space-y-2">
            <Toggle
              checked={form.requireSignature}
              onChange={(value) => set("requireSignature", value)}
              label="A signature is required for returns in this scope"
              hint="Turn off to exempt a family entirely — used for the daily return, whose signing requirement is unconfirmed with the regulator."
            />
            <Toggle
              checked={form.requireSignedPdf}
              onChange={(value) => set("requireSignedPdf", value)}
              label="A cryptographically signed PDF artifact is required"
              hint="Unconfirmed with the regulator; ships off. Turning it on makes PDF signature validity and tamper analysis part of verification."
            />
            <Toggle
              checked={form.distinctSigners}
              onChange={(value) => set("distinctSigners", value)}
              label="Every required signature must come from a different person"
              hint="Segregation of duties. Off only where a scope genuinely has one eligible officer."
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
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <label className="flex items-start gap-2.5 py-1 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 accent-teal"
      />
      <span className="text-body text-navy">
        {label}
        <span className="block text-caption text-slate leading-relaxed">
          {hint}
        </span>
      </span>
    </label>
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
