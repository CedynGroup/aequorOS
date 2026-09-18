"use client";

import type { GrantReasonCategory } from "@aequoros/risk-service-api";

export type GrantReasonDraft = {
  reasonCategory: GrantReasonCategory;
  reasonDetail: string;
  reference: string;
  validUntil: string;
};

export const REASON_OPTIONS = [
  ["new_joiner", "New joiner"],
  ["role_change", "Role change"],
  ["project_engagement", "Project engagement"],
  ["temporary_cover", "Temporary cover"],
  ["regulator_audit_request", "Regulator or audit request"],
  ["incident_break_glass", "Incident break-glass"],
  ["other", "Other"],
] as const satisfies readonly (readonly [GrantReasonCategory, string])[];

export function reasonLabel(category: GrantReasonCategory): string {
  return REASON_OPTIONS.find(([value]) => value === category)?.[1] ?? category;
}

function defaultExpiry(category: GrantReasonCategory): string {
  const date = new Date();
  date.setDate(
    date.getDate() + (category === "incident_break_glass" ? 1 : 30),
  );
  return date.toISOString().slice(0, 16);
}

export function GrantReasonFields({
  value,
  onChange,
  includeExpiry = true,
}: {
  value: GrantReasonDraft;
  onChange: (next: GrantReasonDraft) => void;
  includeExpiry?: boolean;
}) {
  const requiresExpiry =
    value.reasonCategory === "temporary_cover" ||
    value.reasonCategory === "incident_break_glass";
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="block">
        <span className="mb-1.5 block text-caption font-medium text-navy">
          Reason category
        </span>
        <select
          required
          value={value.reasonCategory}
          onChange={(event) => {
            const reasonCategory = event.target.value as GrantReasonCategory;
            const temporary =
              reasonCategory === "temporary_cover" ||
              reasonCategory === "incident_break_glass";
            onChange({
              ...value,
              reasonCategory,
              validUntil: temporary
                ? value.reasonCategory === reasonCategory && value.validUntil
                  ? value.validUntil
                  : defaultExpiry(reasonCategory)
                : "",
            });
          }}
          className="w-full rounded-md border border-border bg-surface px-3 py-2.5 text-body text-navy"
        >
          {REASON_OPTIONS.map(([option, label]) => (
            <option key={option} value={option}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="mb-1.5 block text-caption font-medium text-navy">
          Reference <span className="font-normal text-slate">(optional)</span>
        </span>
        <input
          value={value.reference}
          onChange={(event) =>
            onChange({ ...value, reference: event.target.value })
          }
          maxLength={255}
          placeholder="Ticket, change record or audit ID"
          className="w-full rounded-md border border-border bg-surface px-3 py-2.5 text-body text-navy"
        />
      </label>
      <label className="block sm:col-span-2">
        <span className="mb-1.5 block text-caption font-medium text-navy">
          Detail{" "}
          <span className="font-normal text-slate">
            {value.reasonCategory === "other" ? "(required)" : "(optional)"}
          </span>
        </span>
        <textarea
          required={value.reasonCategory === "other"}
          rows={3}
          value={value.reasonDetail}
          onChange={(event) =>
            onChange({ ...value, reasonDetail: event.target.value })
          }
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-body text-navy"
          placeholder="Add context for the approver"
        />
      </label>
      {includeExpiry && requiresExpiry && (
        <label className="block sm:col-span-2">
          <span className="mb-1.5 block text-caption font-medium text-navy">
            Access expires
          </span>
          <input
            type="datetime-local"
            required
            value={value.validUntil}
            onChange={(event) =>
              onChange({ ...value, validUntil: event.target.value })
            }
            className="w-full rounded-md border border-border bg-surface px-3 py-2.5 text-body text-navy sm:max-w-sm"
          />
        </label>
      )}
    </div>
  );
}
