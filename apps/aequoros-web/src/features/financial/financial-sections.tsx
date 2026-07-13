import type {
  FinancialAccountCreate,
  FinancialAccountUpdate,
  FinancialBalanceCreate,
  FinancialBalanceUpdate,
  FinancialCovenantCreate,
  FinancialCovenantUpdate,
  FinancialDataWorkspaceRead,
  FinancialInstitutionCreate,
  FinancialInstitutionUpdate,
  FinancialObligationCreate,
  FinancialObligationUpdate,
  FinancialReportingPeriodCreate,
  FinancialReportingPeriodUpdate,
  FinancialValidationIssueRead,
} from "@aequoros/risk-service-api";
import {
  AlertCircle,
  CheckCircle2,
  Edit3,
  FileSearch,
  Loader2,
  Plus,
  RotateCcw,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import {
  Alert,
  Badge,
  Button,
  Input,
  Label,
  Textarea,
} from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { cn, labelize, truncateId } from "../../lib/utils";
import type {
  EditableFinancialKind,
  FinancialCreatePayload,
  FinancialMutationResponse,
  FinancialReviewClient,
  FinancialUpdatePayload,
} from "./financial-client";
import { financialErrorMessage } from "./financial-client";

type RecordCollection =
  | "institutions"
  | "accounts"
  | "reportingPeriods"
  | "balances"
  | "cashFlows"
  | "obligations"
  | "covenants";

type FinancialRecord = { id: string };
type FormValues = Record<string, string>;

type FieldConfig = {
  key: string;
  label?: string;
  required?: boolean;
  type?: "text" | "number" | "date" | "select";
  options?: string[];
};

type SectionConfig = {
  collection: RecordCollection;
  table: string;
  title: string;
  singular: string;
  kind?: EditableFinancialKind;
  fields: FieldConfig[];
};

const sections: SectionConfig[] = [
  {
    collection: "institutions",
    table: "financial_institutions",
    title: "Institutions",
    singular: "institution",
    kind: "institution",
    fields: [
      { key: "name", required: true },
      { key: "institutionType", label: "Type" },
      { key: "referenceCode", label: "Reference code" },
    ],
  },
  {
    collection: "accounts",
    table: "financial_accounts",
    title: "Accounts",
    singular: "account",
    kind: "account",
    fields: [
      { key: "accountName", label: "Account name", required: true },
      { key: "accountNumber", label: "Account number" },
      { key: "accountType", label: "Type" },
      { key: "currency" },
      { key: "status" },
      { key: "institutionId", label: "Institution ID" },
    ],
  },
  {
    collection: "reportingPeriods",
    table: "financial_reporting_periods",
    title: "Reporting Periods",
    singular: "reporting period",
    kind: "reportingPeriod",
    fields: [
      { key: "label" },
      {
        key: "periodType",
        label: "Period type",
        required: true,
        type: "select",
        options: ["as_of", "day", "month", "quarter", "year", "custom"],
      },
      { key: "startDate", label: "Start date", type: "date" },
      { key: "endDate", label: "End date", type: "date" },
      { key: "asOfDate", label: "As-of date", type: "date" },
    ],
  },
  {
    collection: "balances",
    table: "financial_balances",
    title: "Balances",
    singular: "balance",
    kind: "balance",
    fields: [
      { key: "balanceType", label: "Balance type", required: true },
      { key: "amount", required: true, type: "number" },
      { key: "currency" },
      { key: "asOfDate", label: "As-of date", type: "date" },
      { key: "accountId", label: "Account ID" },
      { key: "reportingPeriodId", label: "Reporting period ID" },
    ],
  },
  {
    collection: "cashFlows",
    table: "financial_cash_flows",
    title: "Cash Flows",
    singular: "cash flow",
    fields: [
      { key: "cashFlowDate", label: "Date", type: "date" },
      { key: "amount", type: "number" },
      { key: "currency" },
      { key: "direction" },
      { key: "category" },
      { key: "accountId", label: "Account ID" },
      { key: "reportingPeriodId", label: "Reporting period ID" },
    ],
  },
  {
    collection: "obligations",
    table: "financial_obligations",
    title: "Obligations",
    singular: "obligation",
    kind: "obligation",
    fields: [
      { key: "obligationType", label: "Obligation type", required: true },
      { key: "facilityType", label: "Facility type" },
      { key: "principalAmount", label: "Principal", type: "number" },
      { key: "outstandingAmount", label: "Outstanding", type: "number" },
      { key: "currency" },
      { key: "startDate", label: "Start date", type: "date" },
      { key: "maturityDate", label: "Maturity", type: "date" },
      { key: "interestRate", label: "Interest rate", type: "number" },
      { key: "status" },
      { key: "institutionId", label: "Institution ID" },
      { key: "accountId", label: "Account ID" },
      { key: "reportingPeriodId", label: "Reporting period ID" },
    ],
  },
  {
    collection: "covenants",
    table: "financial_covenants",
    title: "Covenants",
    singular: "covenant",
    kind: "covenant",
    fields: [
      { key: "name", required: true },
      { key: "metric", required: true },
      {
        key: "operator",
        required: true,
        type: "select",
        options: ["lt", "lte", "eq", "gte", "gt"],
      },
      { key: "threshold", required: true, type: "number" },
      { key: "actualValue", label: "Actual value", type: "number" },
      {
        key: "complianceStatus",
        label: "Compliance",
        type: "select",
        options: ["unknown", "compliant", "non_compliant"],
      },
      { key: "obligationId", label: "Obligation ID" },
      { key: "reportingPeriodId", label: "Reporting period ID" },
    ],
  },
];

export function FinancialSections({
  workspace,
  mocked,
  tenant,
  caseId,
  client,
  onMutation,
}: {
  workspace: FinancialDataWorkspaceRead;
  mocked: boolean;
  tenant?: TenantHeaders;
  caseId?: string;
  client?: FinancialReviewClient;
  onMutation?: (
    workspace: FinancialDataWorkspaceRead,
  ) => Promise<void> | void;
}) {
  const [focusedCell, setFocusedCell] = useState<string>();
  const activeIssues = workspace.validationIssues.filter(
    (issue) => issue.status !== "resolved",
  );

  function focusIssue(issue: FinancialValidationIssueRead) {
    const recordId = issue.recordId ?? issue.entityId;
    const table = issue.recordTable ?? entityTable(issue.entityType);
    const field = snakeToCamel(issue.field ?? issue.fieldName);
    const cellId =
      recordId && table ? cellIdentifier(table, recordId, field) : undefined;
    const sectionId = table
      ? `financial-section-${table}`
      : "financial-validation";
    const target =
      (cellId && document.getElementById(cellId)) ??
      document.getElementById(sectionId);
    if (!target) return;
    setFocusedCell(cellId);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  }

  return (
    <div className="space-y-4">
      <ValidationPanel
        issues={activeIssues}
        workspace={workspace}
        onSelect={focusIssue}
      />

      <ManualEditHistory edits={workspace.manualEdits} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Canonical financial records</h2>
          <p className="text-xs text-[rgb(var(--muted-foreground))]">
            Grouped by entity type with reporting-period context and source
            traceability.
          </p>
        </div>
        {mocked ? (
          <Badge tone="warning">Editing disabled for demo data</Badge>
        ) : null}
      </div>

      {sections.map((section) => (
        <FinancialSection
          key={section.collection}
          config={section}
          rows={workspace[section.collection] as FinancialRecord[]}
          workspace={workspace}
          mocked={mocked}
          tenant={tenant}
          caseId={caseId}
          client={client}
          onMutation={onMutation}
          focusedCell={focusedCell}
        />
      ))}
    </div>
  );
}

function ManualEditHistory({
  edits,
}: {
  edits: FinancialDataWorkspaceRead["manualEdits"];
}) {
  return (
    <section className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))]">
      <div className="border-b border-[rgb(var(--border))] px-3 py-2">
        <h2 className="text-sm font-semibold">Manual edit history</h2>
        <p className="text-xs text-[rgb(var(--muted-foreground))]">
          Audited corrections with their previous value and reason.
        </p>
      </div>
      {edits.length === 0 ? (
        <div className="px-3 py-4 text-xs text-[rgb(var(--muted-foreground))]">
          No manual edits recorded.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="bg-[rgb(var(--surface-2))]">
                <th className="px-3 py-2 font-medium">Record / field</th>
                <th className="px-3 py-2 font-medium">Previous</th>
                <th className="px-3 py-2 font-medium">New</th>
                <th className="px-3 py-2 font-medium">Reason</th>
                <th className="px-3 py-2 font-medium">Edited by</th>
              </tr>
            </thead>
            <tbody>
              {edits.map((edit) => (
                <tr
                  key={edit.id}
                  className="border-t border-[rgb(var(--border))]"
                >
                  <td className="px-3 py-2">
                    <div>{labelize(edit.recordTable)}</div>
                    <div className="text-[11px] text-[rgb(var(--muted-foreground))]">
                      {labelize(edit.fieldName)} · {truncateId(edit.recordId)}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {renderCell(edit.previousValue)}
                  </td>
                  <td className="px-3 py-2">{renderCell(edit.newValue)}</td>
                  <td className="px-3 py-2">
                    {edit.reason ?? "No reason recorded"}
                  </td>
                  <td className="px-3 py-2">{edit.editedBy ?? "Unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ValidationPanel({
  issues,
  workspace,
  onSelect,
}: {
  issues: FinancialValidationIssueRead[];
  workspace: FinancialDataWorkspaceRead;
  onSelect: (issue: FinancialValidationIssueRead) => void;
}) {
  const summary = workspace.validationSummary;
  return (
    <section
      id="financial-validation"
      tabIndex={-1}
      className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] outline-none focus:ring-2 focus:ring-[rgb(var(--focus))]"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[rgb(var(--border))] px-3 py-2">
        <div>
          <h2 className="text-sm font-semibold">Validation review</h2>
          <p className="text-xs text-[rgb(var(--muted-foreground))]">
            Select an issue to focus its affected record and field.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Validation summary">
          <Badge tone={summary.error ? "danger" : "neutral"}>
            {summary.error} errors
          </Badge>
          <Badge tone={summary.warning ? "warning" : "neutral"}>
            {summary.warning} warnings
          </Badge>
          <Badge tone="info">{summary.info} info</Badge>
          <Badge tone={summary.total ? "neutral" : "success"}>
            {summary.total} total
          </Badge>
        </div>
      </div>
      {issues.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-4 text-sm text-emerald-800">
          <CheckCircle2 className="size-4" /> No active validation issues.
        </div>
      ) : (
        <div className="divide-y divide-[rgb(var(--border))]">
          {issues.map((issue) => (
            <button
              key={issue.id}
              type="button"
              onClick={() => onSelect(issue)}
              className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-[rgb(var(--muted))] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[rgb(var(--focus))]"
            >
              <AlertCircle
                className={cn(
                  "mt-0.5 size-4 shrink-0",
                  issue.severity === "error"
                    ? "text-red-700"
                    : "text-amber-700",
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium">
                  {issue.message}
                </span>
                <span className="block text-[11px] text-[rgb(var(--muted-foreground))]">
                  {labelize(
                    issue.recordTable ?? issue.entityType ?? "workspace",
                  )}{" "}
                  · {labelize(issue.field ?? issue.fieldName)}
                </span>
              </span>
              <Badge
                tone={
                  issue.severity === "error"
                    ? "danger"
                    : issue.severity === "warning"
                      ? "warning"
                      : "info"
                }
              >
                {issue.severity}
              </Badge>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function FinancialSection({
  config,
  rows,
  workspace,
  mocked,
  tenant,
  caseId,
  client,
  onMutation,
  focusedCell,
}: {
  config: SectionConfig;
  rows: FinancialRecord[];
  workspace: FinancialDataWorkspaceRead;
  mocked: boolean;
  tenant?: TenantHeaders;
  caseId?: string;
  client?: FinancialReviewClient;
  onMutation?: (
    workspace: FinancialDataWorkspaceRead,
  ) => Promise<void> | void;
  focusedCell?: string;
}) {
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string>();
  const [sourceId, setSourceId] = useState<string>();
  const [successMessage, setSuccessMessage] = useState<string>();

  return (
    <section
      id={`financial-section-${config.table}`}
      tabIndex={-1}
      className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] outline-none focus:ring-2 focus:ring-[rgb(var(--focus))]"
    >
      <div className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-[rgb(var(--border))] px-3 py-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-[0.04em]">
            {config.title}
          </h3>
          <Badge>{rows.length} records</Badge>
          {!config.kind ? <Badge tone="info">Read only</Badge> : null}
        </div>
        {config.kind && !mocked ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setAdding((value) => !value)}
            aria-expanded={adding}
          >
            <Plus className="size-3.5" /> Add {config.singular}
          </Button>
        ) : null}
      </div>

      {!config.kind ? (
        <div className="border-b border-cyan-200 bg-cyan-50 px-3 py-2 text-xs text-cyan-900">
          Cash-flow mutation contracts do not yet provide required reasons and
          refreshed validation, so this slice intentionally keeps cash flows
          read-only.
        </div>
      ) : null}

      {successMessage ? (
        <output className="block border-b border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
          <CheckCircle2 className="mr-1.5 inline size-3.5" /> {successMessage}
        </output>
      ) : null}

      {adding && config.kind && tenant && caseId && client ? (
        <MutationForm
          mode="create"
          config={config}
          tenant={tenant}
          caseId={caseId}
          client={client}
          onCancel={() => setAdding(false)}
          onMutation={async (response) => {
            await onMutation?.(
              workspaceAfterMutation(workspace, config, response, false),
            );
            setSuccessMessage(
              `Added ${config.singular}. Validation refreshed: ${response.validation.issueCount} issues.`,
            );
            setAdding(false);
          }}
        />
      ) : null}

      {rows.length === 0 ? (
        <div className="px-3 py-8 text-center text-xs text-[rgb(var(--muted-foreground))]">
          No {config.title.toLowerCase()} records.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead>
              <tr className="bg-[rgb(var(--surface-2))]">
                {config.fields.map((field) => (
                  <th
                    key={field.key}
                    className="h-8 px-2 font-medium text-[rgb(var(--muted-foreground))]"
                  >
                    {field.label ?? labelize(field.key)}
                  </th>
                ))}
                <th className="h-8 px-2 text-right font-medium text-[rgb(var(--muted-foreground))]">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const links = workspace.recordSourceLinks.filter(
                  (link) =>
                    link.recordId === row.id &&
                    link.recordTable === config.table,
                );
                return (
                  <FinancialRecordRows
                    key={row.id}
                    row={row}
                    config={config}
                    links={links}
                    workspace={workspace}
                    focusedCell={focusedCell}
                    editing={editingId === row.id}
                    sourceOpen={sourceId === row.id}
                    onEdit={() =>
                      setEditingId(editingId === row.id ? undefined : row.id)
                    }
                    onSource={() =>
                      setSourceId(sourceId === row.id ? undefined : row.id)
                    }
                    mutationProps={
                      config.kind && tenant && caseId && client
                        ? {
                            tenant,
                            caseId,
                            client,
                            onMutation: async (
                              response: FinancialMutationResponse,
                            ) => {
                              await onMutation?.(
                                workspaceAfterMutation(
                                  workspace,
                                  config,
                                  response,
                                  true,
                                ),
                              );
                              setSuccessMessage(
                                `Saved ${config.singular} correction. Validation refreshed: ${response.validation.issueCount} issues.`,
                              );
                              setEditingId(undefined);
                            },
                          }
                        : undefined
                    }
                    canEdit={Boolean(config.kind && !mocked)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FinancialRecordRows({
  row,
  config,
  links,
  workspace,
  focusedCell,
  editing,
  sourceOpen,
  onEdit,
  onSource,
  mutationProps,
  canEdit,
}: {
  row: FinancialRecord;
  config: SectionConfig;
  links: FinancialDataWorkspaceRead["recordSourceLinks"];
  workspace: FinancialDataWorkspaceRead;
  focusedCell?: string;
  editing: boolean;
  sourceOpen: boolean;
  onEdit: () => void;
  onSource: () => void;
  mutationProps?: {
    tenant: TenantHeaders;
    caseId: string;
    client: FinancialReviewClient;
    onMutation: (response: FinancialMutationResponse) => void;
  };
  canEdit: boolean;
}) {
  return (
    <>
      <tr className="border-t border-[rgb(var(--border))]">
        {config.fields.map((field) => {
          const id = cellIdentifier(config.table, row.id, field.key);
          return (
            <td
              key={field.key}
              id={id}
              tabIndex={-1}
              data-financial-field={field.key}
              className={cn(
                "h-10 max-w-52 px-2 outline-none transition-colors focus:ring-2 focus:ring-inset focus:ring-[rgb(var(--focus))]",
                focusedCell === id && "bg-amber-100",
              )}
            >
              {renderCell(
                (row as unknown as Record<string, unknown>)[field.key],
              )}
            </td>
          );
        })}
        <td className="h-10 whitespace-nowrap px-2 text-right">
          <div className="flex justify-end gap-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onSource}
              disabled={links.length === 0}
              aria-expanded={sourceOpen}
            >
              <FileSearch className="size-3.5" /> Source{" "}
              {links.length ? `(${links.length})` : ""}
            </Button>
            {canEdit ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={onEdit}
                aria-expanded={editing}
              >
                <Edit3 className="size-3.5" /> Edit
              </Button>
            ) : null}
          </div>
        </td>
      </tr>
      {sourceOpen ? (
        <tr className="border-t border-[rgb(var(--border))] bg-cyan-50/60">
          <td colSpan={config.fields.length + 1} className="p-3">
            <SourceDrilldown links={links} workspace={workspace} />
          </td>
        </tr>
      ) : null}
      {editing && mutationProps && config.kind ? (
        <tr className="border-t border-[rgb(var(--border))] bg-[rgb(var(--surface-2))]">
          <td colSpan={config.fields.length + 1}>
            <MutationForm
              mode="update"
              record={row}
              config={config}
              onCancel={onEdit}
              {...mutationProps}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function SourceDrilldown({
  links,
  workspace,
}: {
  links: FinancialDataWorkspaceRead["recordSourceLinks"];
  workspace: FinancialDataWorkspaceRead;
}) {
  return (
    <div aria-label="Source traceability" className="space-y-2">
      <div className="text-xs font-semibold uppercase tracking-[0.04em]">
        Source traceability
      </div>
      {links.map((link) => {
        const source = workspace.sourceRows.find(
          (row) => row.id === link.sourceRowId,
        );
        return (
          <div
            key={link.id}
            className="grid gap-2 rounded border border-cyan-200 bg-white p-2 md:grid-cols-3"
          >
            <div>
              <Label>Mapped field</Label>
              <div>{link.fieldName ?? "Whole record"}</div>
              <div className="text-[11px] text-[rgb(var(--muted-foreground))]">
                Confidence: {link.confidence ?? "unknown"}
              </div>
            </div>
            <div>
              <Label>Document / row</Label>
              <div>
                {source?.documentId
                  ? truncateId(source.documentId)
                  : "No document ID"}{" "}
                · row {source?.rowIndex ?? "n/a"}
              </div>
              <code className="break-all text-[10px]">
                {JSON.stringify(source?.locator ?? {})}
              </code>
            </div>
            <div>
              <Label>Raw source value</Label>
              <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-all text-[10px]">
                {JSON.stringify(source?.rawPayload ?? {}, null, 2)}
              </pre>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MutationForm({
  mode,
  record,
  config,
  tenant,
  caseId,
  client,
  onCancel,
  onMutation,
}: {
  mode: "create" | "update";
  record?: FinancialRecord;
  config: SectionConfig & { kind?: EditableFinancialKind };
  tenant: TenantHeaders;
  caseId: string;
  client: FinancialReviewClient;
  onCancel: () => void;
  onMutation: (
    response: FinancialMutationResponse,
  ) => Promise<void> | void;
}) {
  const initialValues = useMemo(
    () => valuesFromRecord(config, record),
    [config, record],
  );
  const [values, setValues] = useState<FormValues>(initialValues);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [mutationSaved, setMutationSaved] = useState(false);
  const [fieldError, setFieldError] = useState<string>();
  const lastSubmit = useRef<(() => Promise<void>) | null>(null);

  async function submit() {
    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setFieldError(
        "A non-empty reason is required for every financial change.",
      );
      return;
    }
    const missing = config.fields.find(
      (field) => field.required && !values[field.key]?.trim(),
    );
    if (missing) {
      setFieldError(`${missing.label ?? labelize(missing.key)} is required.`);
      return;
    }
    const changedFields = changedFieldKeys(config, initialValues, values);
    if (mode === "update" && changedFields.size === 0) {
      setFieldError("Change at least one field before saving a correction.");
      return;
    }
    if (!config.kind || saving) return;
    setFieldError(undefined);
    setError(undefined);
    setSaving(true);
    try {
      const response =
        mode === "create"
          ? await client.create(
              config.kind,
              tenant,
              caseId,
              buildCreatePayload(config.kind, values, trimmedReason),
            )
          : await client.update(
              config.kind,
              tenant,
              caseId,
              record!.id,
              buildUpdatePayload(
                config.kind,
                values,
                changedFields,
                trimmedReason,
              ),
            );
      setMutationSaved(true);
      lastSubmit.current = async () => {
        setSaving(true);
        setError(undefined);
        try {
          await onMutation(response);
        } catch (caught) {
          setError(await financialErrorMessage(caught));
        } finally {
          setSaving(false);
        }
      };
      await onMutation(response);
    } catch (caught) {
      setError(await financialErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  }
  if (!mutationSaved) lastSubmit.current = submit;

  return (
    <form
      className="space-y-3 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      aria-label={`${mode === "create" ? "Add" : "Edit"} ${config.singular}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.04em]">
            {mode === "create"
              ? `Add ${config.singular}`
              : `Correct ${config.singular}`}
          </div>
          <div className="text-[11px] text-[rgb(var(--muted-foreground))]">
            The API records the reason and returns refreshed validation
            atomically.
          </div>
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {config.fields.map((field) => (
          <label key={field.key} className="space-y-1">
            <Label>
              {field.label ?? labelize(field.key)}
              {field.required ? " *" : ""}
            </Label>
            {field.type === "select" ? (
              <select
                aria-label={field.label ?? labelize(field.key)}
                value={values[field.key] ?? ""}
                onChange={(event) => updateField(field.key, event.target.value)}
                className="h-8 w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2.5 text-sm outline-none focus:border-[rgb(var(--focus))]"
              >
                <option value="">Select…</option>
                {field.options?.map((option) => (
                  <option key={option} value={option}>
                    {labelize(option)}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                aria-label={field.label ?? labelize(field.key)}
                type={field.type ?? "text"}
                step={field.type === "number" ? "any" : undefined}
                value={values[field.key] ?? ""}
                onChange={(event) => updateField(field.key, event.target.value)}
              />
            )}
          </label>
        ))}
      </div>
      <div className="block space-y-1">
        <Label>Reason *</Label>
        <Textarea
          aria-label="Reason"
          placeholder="Explain why this manual entry or correction is needed"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </div>
      {fieldError ? (
        <Alert title="Check the form" tone="warning">
          {fieldError}
        </Alert>
      ) : null}
      {error ? (
        <Alert title="Financial change failed" tone="danger">
          <span>
            {mutationSaved ? "Change saved, but refresh failed. " : null}
            {error} Your input has been preserved.
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-2"
            disabled={saving}
            onClick={() => void lastSubmit.current?.()}
          >
            <RotateCcw className="size-3.5" />
            {mutationSaved ? "Retry refresh" : "Retry"}
          </Button>
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={saving}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {saving
            ? "Saving…"
            : mode === "create"
              ? `Add ${config.singular}`
              : "Save correction"}
        </Button>
      </div>
    </form>
  );

  function updateField(key: string, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }
}

function workspaceAfterMutation(
  workspace: FinancialDataWorkspaceRead,
  config: SectionConfig,
  response: FinancialMutationResponse,
  replace: boolean,
): FinancialDataWorkspaceRead {
  const current = workspace[config.collection] as FinancialRecord[];
  const record = response.record as FinancialRecord;
  const records = replace
    ? current.map((item) => (item.id === record.id ? record : item))
    : [...current, record];
  return {
    ...workspace,
    [config.collection]: records,
    validationIssues: response.validation.issues,
    validationSummary: response.validation.summary,
  };
}

function valuesFromRecord(
  config: SectionConfig,
  record?: FinancialRecord,
): FormValues {
  const values = record as unknown as Record<string, unknown> | undefined;
  return Object.fromEntries(
    config.fields.map((field) => [field.key, inputValue(values?.[field.key])]),
  ) as FormValues;
}

function changedFieldKeys(
  config: SectionConfig,
  initialValues: FormValues,
  values: FormValues,
) {
  return new Set(
    config.fields
      .map((field) => field.key)
      .filter(
        (key) =>
          (optional(values, key) ?? null) !==
          (optional(initialValues, key) ?? null),
      ),
  );
}

function inputValue(value: unknown) {
  if (value == null) return "";
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value);
}

function optional(values: FormValues, key: string) {
  const value = values[key]?.trim();
  return value ? value : undefined;
}

function optionalDate(values: FormValues, key: string) {
  return optional(values, key);
}

function buildCreatePayload(
  kind: EditableFinancialKind,
  values: FormValues,
  reason: string,
): FinancialCreatePayload {
  switch (kind) {
    case "institution":
      return {
        name: values.name,
        institutionType: optional(values, "institutionType"),
        referenceCode: optional(values, "referenceCode"),
        reason,
      } as FinancialInstitutionCreate;
    case "account":
      return {
        accountName: values.accountName,
        accountNumber: optional(values, "accountNumber"),
        accountType: optional(values, "accountType"),
        currency: optional(values, "currency"),
        institutionId: optional(values, "institutionId"),
        status: optional(values, "status"),
        reason,
      } as FinancialAccountCreate;
    case "reportingPeriod":
      return {
        label: optional(values, "label"),
        periodType: values.periodType,
        startDate: optionalDate(values, "startDate"),
        endDate: optionalDate(values, "endDate"),
        asOfDate: optionalDate(values, "asOfDate"),
        reason,
      } as FinancialReportingPeriodCreate;
    case "balance":
      return {
        balanceType: values.balanceType,
        amount: values.amount,
        currency: optional(values, "currency"),
        asOfDate: optionalDate(values, "asOfDate"),
        accountId: optional(values, "accountId"),
        reportingPeriodId: optional(values, "reportingPeriodId"),
        reason,
      } as FinancialBalanceCreate;
    case "obligation":
      return {
        obligationType: values.obligationType,
        facilityType: optional(values, "facilityType"),
        principalAmount: optional(values, "principalAmount"),
        outstandingAmount: optional(values, "outstandingAmount"),
        currency: optional(values, "currency"),
        startDate: optionalDate(values, "startDate"),
        maturityDate: optionalDate(values, "maturityDate"),
        interestRate: optional(values, "interestRate"),
        status: optional(values, "status"),
        institutionId: optional(values, "institutionId"),
        accountId: optional(values, "accountId"),
        reportingPeriodId: optional(values, "reportingPeriodId"),
        reason,
      } as FinancialObligationCreate;
    case "covenant":
      return {
        name: values.name,
        metric: values.metric,
        operator: values.operator,
        threshold: values.threshold,
        actualValue: optional(values, "actualValue"),
        complianceStatus: optional(values, "complianceStatus"),
        obligationId: optional(values, "obligationId"),
        reportingPeriodId: optional(values, "reportingPeriodId"),
        reason,
      } as FinancialCovenantCreate;
  }
}

function buildUpdatePayload(
  kind: EditableFinancialKind,
  values: FormValues,
  changedFields: ReadonlySet<string>,
  reason: string,
): FinancialUpdatePayload {
  const changed = (key: string) =>
    changedFields.has(key) ? (optional(values, key) ?? null) : undefined;
  switch (kind) {
    case "institution":
      return {
        name: changed("name"),
        institutionType: changed("institutionType"),
        referenceCode: changed("referenceCode"),
        reason,
      } as FinancialInstitutionUpdate;
    case "account":
      return {
        accountName: changed("accountName"),
        accountNumber: changed("accountNumber"),
        accountType: changed("accountType"),
        currency: changed("currency"),
        institutionId: changed("institutionId"),
        status: changed("status"),
        reason,
      } as FinancialAccountUpdate;
    case "reportingPeriod":
      return {
        label: changed("label"),
        periodType: changed("periodType"),
        startDate: changed("startDate"),
        endDate: changed("endDate"),
        asOfDate: changed("asOfDate"),
        reason,
      } as FinancialReportingPeriodUpdate;
    case "balance":
      return {
        balanceType: changed("balanceType"),
        amount: changed("amount"),
        currency: changed("currency"),
        asOfDate: changed("asOfDate"),
        accountId: changed("accountId"),
        reportingPeriodId: changed("reportingPeriodId"),
        reason,
      } as FinancialBalanceUpdate;
    case "obligation":
      return {
        obligationType: changed("obligationType"),
        facilityType: changed("facilityType"),
        principalAmount: changed("principalAmount"),
        outstandingAmount: changed("outstandingAmount"),
        currency: changed("currency"),
        startDate: changed("startDate"),
        maturityDate: changed("maturityDate"),
        interestRate: changed("interestRate"),
        status: changed("status"),
        institutionId: changed("institutionId"),
        accountId: changed("accountId"),
        reportingPeriodId: changed("reportingPeriodId"),
        reason,
      } as FinancialObligationUpdate;
    case "covenant":
      return {
        name: changed("name"),
        metric: changed("metric"),
        operator: changed("operator"),
        threshold: changed("threshold"),
        actualValue: changed("actualValue"),
        complianceStatus: changedFields.has("complianceStatus")
          ? optional(values, "complianceStatus")
          : undefined,
        obligationId: changed("obligationId"),
        reportingPeriodId: changed("reportingPeriodId"),
        reason,
      } as FinancialCovenantUpdate;
  }
}

function entityTable(entityType: string | null) {
  const mapping: Record<string, string> = {
    institution: "financial_institutions",
    account: "financial_accounts",
    reporting_period: "financial_reporting_periods",
    balance: "financial_balances",
    cash_flow: "financial_cash_flows",
    obligation: "financial_obligations",
    covenant: "financial_covenants",
  };
  return entityType ? mapping[entityType] : undefined;
}

function snakeToCamel(value: string) {
  return value.replace(/_([a-z])/g, (_, letter: string) =>
    letter.toUpperCase(),
  );
}

function cellIdentifier(table: string, recordId: string, field: string) {
  return `financial-${table}-${recordId}-${snakeToCamel(field)}`;
}

function renderCell(value: unknown) {
  if (value == null || value === "")
    return <span className="text-[rgb(var(--muted-foreground))]">None</span>;
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  if (typeof value === "object")
    return (
      <code className="break-all text-[10px]">{JSON.stringify(value)}</code>
    );
  if (
    typeof value === "string" &&
    value.length > 20 &&
    /^[0-9a-f-]{16,}$/i.test(value)
  )
    return (
      <span
        title={value}
        className="rounded bg-[rgb(var(--muted))] px-1.5 py-0.5 font-mono text-[10px]"
      >
        {truncateId(value)}
      </span>
    );
  return String(value);
}
