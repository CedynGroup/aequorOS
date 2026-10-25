"use client";

import { useState } from "react";
import {
  CfpAudience,
  CfpHorizon,
  type CfpContentInput,
  type CfpSummaryRead,
} from "@aequoros/risk-service-api";
import { wholeInstitutionCapabilities } from "@/lib/api/dataScope";
import { useBankContext } from "@/components/shell/BankContext";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { inputCls } from "@/components/institution/shared";
import { hasEffectiveCapability, hasStructuralCapability } from "@/lib/modules";
import {
  useCfpSummary,
  useCfpLifecycle,
  useEwiDashboard,
} from "@/lib/api/hooks";
import { fmtDateUTC } from "@/lib/api/values";
import { cfpContentForComparison } from "@/lib/api/cfp";
import { isApiError } from "@/lib/api/client";

const names: Record<string, string> = {
  intraday: "Intraday",
  up_to_1m: "Up to 1 month",
  "1_to_3m": "1 to 3 months",
  "3_to_12m": "3 to 12 months",
  over_12m: "Over 12 months",
  depositRunoffUpliftPct: "Deposit runoff uplift (%)",
  fundingCostUpliftBps: "Funding cost uplift (bps)",
};
const label = (value: string) => {
  const words = value
    .replaceAll(/([A-Z])/g, " $1")
    .replaceAll("_", " ")
    .toLowerCase();
  return names[value] ?? words.charAt(0).toUpperCase() + words.slice(1);
};
type Field = {
  key: string;
  title?: string;
  options?: readonly string[];
  choices?: { code: string; name: string }[];
  optional?: boolean;
  numeric?: boolean;
  step?: number | "any";
  min?: number;
  max?: number;
};

/** Repeatable, labelled inputs keep each directive content block editable. */
function Rows<T extends object>({
  title,
  rows,
  fields,
  empty,
  onChange,
}: {
  title: string;
  rows: T[];
  fields: Field[];
  empty: T;
  onChange: (rows: T[]) => void;
}) {
  return (
    <fieldset className="space-y-3 rounded border border-border-light p-4">
      <legend className="px-1 font-medium">{title}</legend>
      {rows.map((row, index) => (
        <div key={index} className="grid gap-3 sm:grid-cols-2">
          {fields.map((field) => (
            <label key={field.key} className="text-caption">
              {field.title ?? label(field.key)}
              {field.optional ? " (optional)" : ""}
              {field.options ? (
                <select
                  className={inputCls}
                  value={String(Reflect.get(row, field.key) ?? "")}
                  onChange={(event) =>
                    onChange(
                      rows.map((item, i) =>
                        i === index
                          ? { ...item, [field.key]: event.target.value }
                          : item,
                      ),
                    )
                  }
                >
                  {field.options.map((value) => (
                    <option key={value} value={value}>
                      {label(value)}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  list={field.choices ? `cfp-indicators-${index}` : undefined}
                  className={inputCls}
                  required={!field.optional}
                  type={field.numeric ? "number" : "text"}
                  step={field.numeric ? (field.step ?? 1) : undefined}
                  min={field.min}
                  max={field.max}
                  value={String(Reflect.get(row, field.key) ?? "")}
                  onChange={(event) =>
                    onChange(
                      rows.map((item, i) =>
                        i === index
                          ? {
                              ...item,
                              [field.key]: field.numeric
                                ? Number(event.target.value)
                                : event.target.value,
                            }
                          : item,
                      ),
                    )
                  }
                />
              )}
              {field.choices && (
                <datalist id={`cfp-indicators-${index}`}>
                  {field.choices.map((choice) => (
                    <option key={choice.code} value={choice.code}>
                      {choice.name}
                    </option>
                  ))}
                </datalist>
              )}
            </label>
          ))}
          <button
            type="button"
            className="text-caption text-critical text-left"
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            Remove row {index + 1} from {title.toLowerCase()}
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn-secondary"
        onClick={() => onChange([...rows, { ...empty }])}
      >
        Add {title.toLowerCase()}
      </button>
    </fieldset>
  );
}

export default function CfpLifecycle() {
  const { bank } = useBankContext();
  const summary = useCfpSummary(bank?.id);
  return (
    <QueryBoundary
      isLoading={summary.isLoading}
      error={summary.error}
      onRetry={() => summary.refetch()}
    >
      {summary.data && (
        <CfpEditor
          key={`${bank?.id}:${summary.data.current?.revision ?? "new"}`}
          summary={summary.data}
          onReload={() => void summary.refetch()}
        />
      )}
    </QueryBoundary>
  );
}

function CfpEditor({
  summary,
  onReload,
}: {
  summary: CfpSummaryRead;
  onReload: () => void;
}) {
  const { bank, period, institutionCapabilities, authorityPending } =
    useBankContext();
  const { profile } = useUserProfile();
  const mutation = useCfpLifecycle(bank?.id);
  const ewis = useEwiDashboard(bank?.id, period?.id);
  const current = summary.current;
  const approved = summary.approved;
  const [content, setContent] = useState<CfpContentInput>(
    current?.content ?? {},
  );
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");
  const [message, setMessage] = useState("");
  const canDraft =
    !authorityPending &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(institutionCapabilities),
      "liq",
      "confidential",
      current?.status === "draft" ? "edit" : "create",
    );
  const canApprove =
    !authorityPending &&
    hasStructuralCapability(
      wholeInstitutionCapabilities(institutionCapabilities),
      "liq",
      "confidential",
      "approve",
    );
  const selfApproval =
    !!current?.preparedBy && current.preparedBy === profile?.userId;
  const hasUnsavedContent =
    JSON.stringify(cfpContentForComparison(content)) !==
    JSON.stringify(cfpContentForComparison(current?.content ?? {}));
  const staleRevision =
    isApiError(mutation.error) &&
    mutation.error.errorCode === "stale_cfp_revision";
  const approveReason = !canApprove
    ? "Requires Liquidity · Confidential · Approve authority."
    : staleRevision
      ? "Reload the latest plan and review it before approving."
      : selfApproval
        ? "The preparer cannot approve their own plan. Ask an independent approver."
        : current?.status !== "draft"
          ? "Save a draft before Board approval."
          : hasUnsavedContent
            ? "Save your content changes before Board approval. Saving makes you the preparer and requires an independent approver."
            : undefined;
  const activateReason = !canApprove
    ? "Requires Liquidity · Confidential · Approve authority."
    : !period
      ? "Select an as-of reporting period."
      : !approved
        ? "Record Board approval before activation."
        : approved.active
          ? "The approved plan is already active."
          : approved.approvalOverdue || !approved.approvalExpiresAt
            ? "Renew Board approval before activation."
            : undefined;
  const standDownReason = !canApprove
    ? "Requires Liquidity · Confidential · Approve authority."
    : !period
      ? "Select an as-of reporting period."
      : !approved?.active
        ? "There is no active plan to stand down."
        : undefined;

  async function act(action: "draft" | "approve" | "activate" | "standDown") {
    if (
      !reason.trim() ||
      (action === "draft" && !canDraft) ||
      (action === "approve" && (approveReason || !reference.trim())) ||
      (action === "activate" && activateReason) ||
      (action === "standDown" && standDownReason)
    )
      return;
    setMessage("");
    try {
      if (action === "draft")
        await mutation.mutateAsync({
          action,
          payload: { content, reason: reason.trim() },
        });
      else if (action === "approve")
        await mutation.mutateAsync({
          action,
          payload: {
            expectedRevision: current!.revision,
            approvalReference: reference.trim(),
            reason: reason.trim(),
          },
        });
      else if (period)
        await mutation.mutateAsync({
          action,
          payload: { reportingPeriodId: period.id, reason: reason.trim() },
        });
      setMessage(
        "CFP action recorded. The plan and event log have been refreshed.",
      );
      setReason("");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "The CFP action could not be recorded.",
      );
    }
  }
  function actionButton(
    action: "approve" | "activate" | "standDown",
    text: string,
    refusal?: string,
  ) {
    const button = (id?: string) => (
      <button
        type="button"
        className="btn-secondary min-h-11 rounded border border-border px-4 py-2 font-medium disabled:opacity-50"
        aria-describedby={id}
        disabled={
          !!refusal ||
          mutation.isPending ||
          !reason.trim() ||
          (action === "approve" && !reference.trim())
        }
        onClick={() => void act(action)}
      >
        {text}
      </button>
    );
    return (
      <div className="space-y-2">
        {refusal ? (
          <DisabledWithReason reason={refusal}>{button}</DisabledWithReason>
        ) : (
          button()
        )}
        {refusal && (
          <p className="max-w-sm text-caption text-slate">{refusal}</p>
        )}
      </div>
    );
  }
  return (
    <SectionCard
      title="Manage contingency funding plan"
      subtitle="Draft, independent Board approval, activation and stand-down. All changes require a reason."
    >
      <div className="space-y-4">
        <p className="text-body">
          Current plan:{" "}
          {current
            ? `v${current.version} · ${current.status}`
            : "No draft or approved plan"}
          . Board approval: {approved?.approvalReference ?? "Not recorded"}.
        </p>
        <p className="text-body text-navy">
          Approval validity:{" "}
          {approved?.approvalOverdue
            ? "Overdue. Renew Board approval."
            : approved?.approvalExpiresAt
              ? `Valid until ${fmtDateUTC(new Date(approved.approvalExpiresAt))}.`
              : "No current approval recorded."}{" "}
          Next action:{" "}
          {!current
            ? "Author and save a draft."
            : current.status === "draft"
              ? "Review the draft and obtain independent Board approval."
              : approved?.active
                ? "Monitor indicators and stand down when appropriate."
                : "Review evidence and activate when required."}
        </p>
        <p className="text-caption text-slate">
          Activation and stand-down capture indicators for{" "}
          {period
            ? fmtDateUTC(period.periodEnd)
            : "the selected reporting period (none selected)"}
          . aequorOS records an internal regulator notification; it does not
          transmit it externally.
        </p>
        <div className="rounded border border-border-light bg-surface p-4 space-y-3">
          <p className="font-medium text-navy">Board approval and activation</p>
          <label className="block text-caption">
            Board approval reference
            <input
              maxLength={200}
              className={inputCls}
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            {actionButton("approve", "Record Board approval", approveReason)}
            {actionButton("activate", "Activate CFP", activateReason)}
            {actionButton("standDown", "Stand down CFP", standDownReason)}
          </div>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void act("draft");
          }}
          className="space-y-4"
        >
          <details open className="rounded border border-border-light p-4">
            <summary className="cursor-pointer font-medium text-navy">
              Edit plan content
            </summary>
            <fieldset
              disabled={!canDraft || mutation.isPending}
              className="space-y-4"
            >
              <Rows
                title="EWI triggers"
                rows={content.ewiTriggers ?? []}
                fields={[
                  {
                    key: "indicatorCode",
                    title: "Indicator code (from the EWI register)",
                    choices: ewis.data?.indicators,
                  },
                  { key: "triggerCondition" },
                  { key: "notes", optional: true },
                ]}
                empty={{ indicatorCode: "", triggerCondition: "" }}
                onChange={(ewiTriggers) =>
                  setContent({ ...content, ewiTriggers })
                }
              />
              <Rows
                title="Funding options"
                rows={content.fundingOptions ?? []}
                fields={[
                  { key: "horizon", options: Object.values(CfpHorizon) },
                  { key: "source" },
                  { key: "estimatedCapacity", optional: true },
                  { key: "leadTime", optional: true },
                  { key: "notes", optional: true },
                ]}
                empty={{ horizon: "intraday", source: "" }}
                onChange={(fundingOptions) =>
                  setContent({ ...content, fundingOptions })
                }
              />
              <Rows
                title="Action plans"
                rows={content.actionPlans ?? []}
                fields={[
                  { key: "side", options: ["asset", "liability"] },
                  { key: "action" },
                  { key: "owner" },
                  { key: "timeline", optional: true },
                ]}
                empty={{ side: "asset", action: "", owner: "" }}
                onChange={(actionPlans) =>
                  setContent({ ...content, actionPlans })
                }
              />
              <Rows
                title="Alternative sources"
                rows={content.alternativeSources ?? []}
                fields={[
                  { key: "source" },
                  { key: "conditions", optional: true },
                  { key: "notes", optional: true },
                ]}
                empty={{ source: "" }}
                onChange={(alternativeSources) =>
                  setContent({ ...content, alternativeSources })
                }
              />
              <Rows
                title="Escalation procedures"
                rows={content.escalationProcedures ?? []}
                fields={[
                  { key: "priority", numeric: true, min: 1, max: 99 },
                  { key: "stage" },
                  { key: "trigger" },
                  { key: "actions" },
                  { key: "owner" },
                ]}
                empty={{
                  priority: 1,
                  stage: "",
                  trigger: "",
                  actions: "",
                  owner: "",
                }}
                onChange={(escalationProcedures) =>
                  setContent({ ...content, escalationProcedures })
                }
              />
              <Rows
                title="Key relationships"
                rows={content.keyRelationships ?? []}
                fields={[
                  { key: "institution" },
                  { key: "role" },
                  { key: "contactName", optional: true },
                  { key: "phone", optional: true },
                  { key: "email", optional: true },
                  { key: "notes", optional: true },
                ]}
                empty={{ institution: "", role: "" }}
                onChange={(keyRelationships) =>
                  setContent({ ...content, keyRelationships })
                }
              />
              <Rows
                title="Communication plans"
                rows={content.communicationPlans ?? []}
                fields={[
                  { key: "audience", options: Object.values(CfpAudience) },
                  { key: "channel" },
                  { key: "owner" },
                  { key: "messageOutline", optional: true },
                ]}
                empty={{ audience: "regulator", channel: "", owner: "" }}
                onChange={(communicationPlans) =>
                  setContent({ ...content, communicationPlans })
                }
              />
              <Rows
                title="Behavioral liquidity scenarios"
                rows={content.behavioralLiquidityScenarios ?? []}
                fields={[
                  { key: "name" },
                  {
                    key: "activationHorizon",
                    options: Object.values(CfpHorizon),
                  },
                  { key: "linkedAction" },
                  { key: "depositRunoffUpliftPct", numeric: true, step: "any" },
                  { key: "fundingCostUpliftBps", numeric: true, step: "any" },
                  { key: "notes", optional: true },
                ]}
                empty={{
                  name: "",
                  activationHorizon: "intraday",
                  linkedAction: "",
                  depositRunoffUpliftPct: 0,
                  fundingCostUpliftBps: 0,
                }}
                onChange={(behavioralLiquidityScenarios) =>
                  setContent({ ...content, behavioralLiquidityScenarios })
                }
              />
            </fieldset>
          </details>
          <div className="sticky bottom-0 z-10 space-y-3 border-t border-border bg-surface p-4 shadow-sm">
            <label className="block text-caption">
              Reason / evidence
              <textarea
                required
                maxLength={2000}
                className={inputCls}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <DisabledWithReason
              reason={
                !canDraft
                  ? "Requires Liquidity · Confidential · Create for a new version or Edit for a draft."
                  : "Enter a reason before saving."
              }
            >
              {(id) => (
                <button
                  className="btn-primary min-h-11 rounded px-5 py-2 font-medium disabled:opacity-50"
                  aria-describedby={id}
                  disabled={!canDraft || mutation.isPending || !reason.trim()}
                >
                  Save draft
                </button>
              )}
            </DisabledWithReason>
            {!canDraft && (
              <p className="text-caption text-slate">
                Requires Liquidity · Confidential · Create for a new version or
                Edit for a draft.
              </p>
            )}
          </div>
        </form>
        {!reason.trim() && (
          <p className="text-caption text-slate">
            Enter a reason / evidence to enable an authorized action. Board
            approval also needs a reference.
          </p>
        )}
        {message && <p role="status">{message}</p>}
        {staleRevision && (
          <button type="button" className="btn-secondary" onClick={onReload}>
            Reload latest plan
          </button>
        )}
        {mutation.isPending && <p role="status">Recording CFP action…</p>}
      </div>
    </SectionCard>
  );
}
