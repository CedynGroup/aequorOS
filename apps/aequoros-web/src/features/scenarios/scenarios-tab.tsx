import type {
  AssumptionCategory,
  AssumptionValue,
  ScenarioAssumptionRead,
  ScenarioRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Fragment,
  type ComponentPropsWithoutRef,
  useEffect,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import {
  Alert,
  Badge,
  Button,
  Input,
  Label,
  Panel,
  PanelHeader,
  Skeleton,
  Textarea,
} from "../../components/ui";
import { riskApi, type TenantHeaders } from "../../lib/api";
import { labelize } from "../../lib/utils";
import {
  focusWorkspaceTarget,
  workspaceHash,
} from "../../lib/workspace-deep-link";
import { ErrorPanel } from "../../shared/route-ui";

const categories: AssumptionCategory[] = [
  "growth",
  "expenses",
  "cash_flow_timing",
  "credit_usage",
  "repayment_behavior",
  "other",
];
type AssumptionValueType = "string" | "number" | "boolean" | "null";
const valueTypes: AssumptionValueType[] = [
  "string",
  "number",
  "boolean",
  "null",
];

export function ScenariosTab({
  tenant,
  caseId,
  mutationDisabled = false,
}: {
  tenant: TenantHeaders;
  caseId: string;
  mutationDisabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const deepLink = scenarioDeepLink();
  const focusedDeepLinks = useRef(new Set<string>());
  const includeArchived = deepLink !== null;
  const queryKey = ["scenarios", tenant, caseId, includeArchived] as const;
  const query = useQuery({
    queryKey,
    queryFn: () => riskApi.scenarios(tenant, caseId, includeArchived),
  });
  const [selectedId, setSelectedId] = useState(
    () => deepLink?.scenarioId ?? "",
  );
  const [customName, setCustomName] = useState("");
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    if (!selectedId && query.data?.scenarios[0])
      setSelectedId(query.data.scenarios[0].id);
  }, [query.data, selectedId]);

  useEffect(() => {
    if (
      query.data &&
      deepLink?.targetId &&
      !focusedDeepLinks.current.has(deepLink.targetId) &&
      focusWorkspaceTarget(deepLink.targetId)
    ) {
      focusedDeepLinks.current.add(deepLink.targetId);
    }
  }, [deepLink?.targetId, query.data, selectedId]);

  const refresh = async (message: string) => {
    setSavedMessage(message);
    toast.success(message);
    await queryClient.invalidateQueries({ queryKey });
    await queryClient.invalidateQueries({
      queryKey: ["scenario-validation", tenant, caseId],
    });
  };
  const initialize = useMutation({
    mutationFn: () =>
      riskApi.initializeScenarios(tenant, caseId, {
        reason: "Initialize scenario workspace",
      }),
    onSuccess: async (workspace) => {
      setSelectedId(workspace.scenarios[0]?.id ?? "");
      await refresh("Baseline and downside scenarios initialized");
    },
  });
  const create = useMutation({
    mutationFn: () =>
      riskApi.createScenario(tenant, caseId, {
        name: customName,
        reason: "Create custom scenario",
      }),
    onSuccess: async (result) => {
      setSelectedId(result.scenario.id);
      setCustomName("");
      await refresh("Custom scenario created");
    },
  });

  if (query.isLoading) return <Skeleton className="h-96" />;
  if (query.isError) return <ErrorPanel error={query.error} />;
  const workspace = query.data;
  if (!workspace)
    return <Alert title="Scenario workspace unavailable" tone="danger" />;
  if (!workspace.scenarios.length) {
    return (
      <Panel>
        <PanelHeader
          title="Scenario assumptions"
          meta="No scenarios configured"
        />
        <div className="space-y-3 p-4">
          <Alert title="No scenarios yet">
            Initialize the baseline and downside scenarios before calculations
            can run.
          </Alert>
          <Button
            onClick={() => initialize.mutate()}
            disabled={mutationDisabled || initialize.isPending}
          >
            {initialize.isPending
              ? "Initializing…"
              : "Initialize baseline and downside"}
          </Button>
          {initialize.isError ? <ErrorPanel error={initialize.error} /> : null}
        </div>
      </Panel>
    );
  }

  const selected =
    workspace.scenarios.find((scenario) => scenario.id === selectedId) ??
    workspace.scenarios[0];
  const auditMode = selected.archivedAt !== null || mutationDisabled;
  return (
    <div className="@container/scenarios space-y-3">
      {savedMessage ? (
        <Alert title="Saved successfully">{savedMessage}</Alert>
      ) : null}
      <div className="grid gap-3 @4xl/scenarios:grid-cols-[240px_minmax(0,1fr)]">
        <Panel>
          <PanelHeader
            title="Scenarios"
            meta={`${workspace.readiness.completeScenarioCount}/${workspace.readiness.scenarioCount} calculation-ready`}
          />
          <div className="space-y-2 p-3">
            <Badge tone={workspace.readiness.ready ? "success" : "warning"}>
              {workspace.readiness.ready
                ? "Ready for calculations"
                : "Validation required"}
            </Badge>
            {workspace.scenarios.map((scenario) => (
              <button
                key={scenario.id}
                type="button"
                className={`block w-full rounded-md border p-2 text-left text-sm ${
                  selected.id === scenario.id
                    ? "border-[rgb(var(--primary))] bg-[rgb(var(--muted))]"
                    : "border-[rgb(var(--border))]"
                }`}
                onClick={() => setSelectedId(scenario.id)}
              >
                <span className="block font-medium">{scenario.name}</span>
                <span className="text-xs text-[rgb(var(--muted-foreground))]">
                  {labelize(scenario.scenarioType)} ·{" "}
                  {scenario.assumptions.length} assumptions
                  {scenario.archivedAt ? " · Archived" : ""}
                </span>
              </button>
            ))}
            {!auditMode ? (
              <div className="border-t border-[rgb(var(--border))] pt-3">
                <Label>Custom scenario name</Label>
                <Input
                  aria-label="Custom scenario name"
                  value={customName}
                  onChange={(event) => setCustomName(event.target.value)}
                  placeholder="Management case"
                />
                <Button
                  className="mt-2 w-full"
                  variant="outline"
                  disabled={!customName.trim() || create.isPending}
                  onClick={() => create.mutate()}
                >
                  Create custom scenario
                </Button>
                {create.isError ? (
                  <div className="mt-2">
                    <ErrorPanel error={create.error} />
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </Panel>
        <ScenarioEditor
          key={selected.id}
          tenant={tenant}
          caseId={caseId}
          scenario={selected}
          readOnly={auditMode}
          onSaved={refresh}
          onSelected={setSelectedId}
        />
      </div>
    </div>
  );
}

function ScenarioEditor({
  tenant,
  caseId,
  scenario,
  readOnly,
  onSaved,
  onSelected,
}: {
  tenant: TenantHeaders;
  caseId: string;
  scenario: ScenarioRead;
  readOnly: boolean;
  onSaved: (message: string) => Promise<void>;
  onSelected: (id: string) => void;
}) {
  const validation = useQuery({
    queryKey: ["scenario-validation", tenant, caseId, scenario.id],
    queryFn: () => riskApi.scenarioValidation(tenant, caseId, scenario.id),
  });
  const [copyName, setCopyName] = useState(`${scenario.name} copy`);
  const [name, setName] = useState(scenario.name);
  const [description, setDescription] = useState(scenario.description ?? "");
  const [savedDetails, setSavedDetails] = useState({
    name: scenario.name,
    description: scenario.description ?? "",
  });
  const [newAssumption, setNewAssumption] = useState({
    category: "other" as AssumptionCategory,
    key: "",
    label: "",
    value: "",
    valueType: "string" as AssumptionValueType,
    unit: "",
  });
  const updateScenario = useMutation({
    mutationFn: () => {
      const changes = {
        ...(name !== savedDetails.name ? { name } : {}),
        ...(description !== savedDetails.description
          ? { description: description || null }
          : {}),
        reason: "Update scenario details",
      };
      return riskApi.updateScenario(tenant, caseId, scenario.id, changes);
    },
    onSuccess: async (result) => {
      const details = {
        name: result.scenario.name,
        description: result.scenario.description ?? "",
      };
      setName(details.name);
      setDescription(details.description);
      setSavedDetails(details);
      await onSaved("Scenario details saved");
    },
  });
  const copy = useMutation({
    mutationFn: () =>
      riskApi.copyScenario(tenant, caseId, scenario.id, {
        name: copyName,
        reason: "Copy scenario for review",
      }),
    onSuccess: async (result) => {
      onSelected(result.scenario.id);
      await onSaved("Scenario copied");
    },
  });
  const archive = useMutation({
    mutationFn: () =>
      riskApi.archiveScenario(tenant, caseId, scenario.id, {
        reason: "Archive scenario from workspace",
      }),
    onSuccess: async () => {
      onSelected("");
      await onSaved("Scenario archived");
    },
  });
  const add = useMutation({
    mutationFn: () =>
      riskApi.createAssumption(tenant, caseId, scenario.id, {
        category: newAssumption.category,
        key: newAssumption.key,
        label: newAssumption.label,
        value: typedUnitValue(
          newAssumption.value,
          newAssumption.valueType,
          newAssumption.unit,
        ),
        unit: newAssumption.unit || undefined,
        reason: "Add scenario assumption",
      }),
    onSuccess: async () => {
      setNewAssumption({
        category: "other",
        key: "",
        label: "",
        value: "",
        valueType: "string",
        unit: "",
      });
      await onSaved("Assumption added");
    },
  });
  const actionError =
    updateScenario.error ?? copy.error ?? archive.error ?? add.error;
  const validationPassed = validation.data
    ? readOnly
      ? validation.data.issueCount === 0
      : validation.data.complete
    : false;

  return (
    <Panel className="@container/editor min-w-0">
      <PanelHeader
        title={scenario.name}
        meta={`${labelize(scenario.scenarioType)} scenario`}
        actions={
          <div className="flex gap-2">
            {readOnly ? <Badge tone="warning">Archived</Badge> : null}
            <Badge tone="info">{scenario.assumptions.length} inputs</Badge>
          </div>
        }
      />
      <div className="space-y-4 p-3">
        {readOnly ? (
          <Alert title="Archived scenario audit mode">
            This historical scenario is read-only.
          </Alert>
        ) : null}
        <div className="grid gap-2 @2xl/editor:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] @2xl/editor:items-end">
          <div>
            <Label>Scenario name</Label>
            <Input
              aria-label="Scenario name"
              value={name}
              readOnly={readOnly}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <Label>Scenario description</Label>
            <Textarea
              aria-label="Scenario description"
              className="min-h-8"
              value={description}
              readOnly={readOnly}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          {!readOnly ? (
            <Button
              variant="outline"
              disabled={
                !name.trim() ||
                updateScenario.isPending ||
                (name === savedDetails.name &&
                  description === savedDetails.description)
              }
              onClick={() => updateScenario.mutate()}
            >
              Save details
            </Button>
          ) : null}
        </div>
        {validation.isLoading ? <Skeleton className="h-16" /> : null}
        {validation.isError ? <ErrorPanel error={validation.error} /> : null}
        {validationPassed ? (
          <Alert title="Scenario validation passed">
            All required assumptions are present and reviewed.
          </Alert>
        ) : validation.data ? (
          <Alert
            title={`${validation.data.issueCount} validation issues`}
            tone="warning"
          >
            <ul className="list-disc space-y-1 pl-4">
              {validation.data.issues.slice(0, 6).map((issue) => (
                <li
                  key={`${issue.code}-${issue.assumptionId ?? issue.category ?? "scenario"}`}
                >
                  {issue.message}
                </li>
              ))}
            </ul>
          </Alert>
        ) : null}
        {!readOnly ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-52 flex-1">
              <Label>Copy name</Label>
              <Input
                aria-label="Copy scenario name"
                value={copyName}
                onChange={(event) => setCopyName(event.target.value)}
              />
            </div>
            <Button
              variant="outline"
              disabled={!copyName.trim() || copy.isPending}
              onClick={() => copy.mutate()}
            >
              Copy scenario
            </Button>
            <Button
              variant="danger"
              disabled={archive.isPending}
              onClick={() => archive.mutate()}
            >
              Archive scenario
            </Button>
          </div>
        ) : null}
        {actionError ? <ErrorPanel error={actionError} /> : null}
        <div
          data-testid="assumption-table"
          className="max-w-full overflow-x-auto rounded-md border border-[rgb(var(--border))]"
        >
          {scenario.assumptions.length ? (
            <table className="w-full min-w-[720px] table-fixed border-collapse text-left text-xs">
              <caption className="sr-only">Scenario assumptions</caption>
              <colgroup>
                <col className="w-[27%]" />
                <col className="w-[12%]" />
                <col className="w-[20%]" />
                <col className="w-[10%]" />
                <col className="w-[13%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-[rgb(var(--border))] bg-[rgb(var(--surface-2))] text-[rgb(var(--muted-foreground))]">
                  {["Label", "Type", "Value", "Unit", "Status", "Actions"].map(
                    (heading) => (
                      <th
                        key={heading}
                        scope="col"
                        className="h-9 px-2 text-[11px] font-semibold uppercase tracking-[0.04em]"
                      >
                        {heading}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {scenario.assumptions.map((assumption) => (
                  <AssumptionRow
                    key={assumption.id}
                    tenant={tenant}
                    caseId={caseId}
                    scenarioId={scenario.id}
                    assumption={assumption}
                    readOnly={readOnly}
                    onSaved={onSaved}
                  />
                ))}
              </tbody>
            </table>
          ) : (
            <div className="p-3">
              <Alert title="No assumptions">
                Add required growth, expense, cash-flow timing, credit usage,
                and repayment inputs.
              </Alert>
            </div>
          )}
        </div>
        {!readOnly ? (
          <div className="rounded-md border border-dashed border-[rgb(var(--border))] p-3">
            <div className="mb-2 text-sm font-medium">Add assumption</div>
            <div className="grid gap-2 @2xl/editor:grid-cols-3 @4xl/editor:grid-cols-6">
              <select
                aria-label="Assumption category"
                className="h-8 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2 text-sm"
                value={newAssumption.category}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    category: event.target.value as AssumptionCategory,
                  })
                }
              >
                {categories.map((category) => (
                  <option key={category} value={category}>
                    {labelize(category)}
                  </option>
                ))}
              </select>
              <Input
                aria-label="Assumption key"
                placeholder="input_key"
                value={newAssumption.key}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    key: event.target.value,
                  })
                }
              />
              <Input
                aria-label="Assumption label"
                placeholder="Label"
                value={newAssumption.label}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    label: event.target.value,
                  })
                }
              />
              <select
                aria-label="New assumption value type"
                className="h-8 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2 text-sm"
                value={newAssumption.valueType}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    valueType: event.target.value as AssumptionValueType,
                  })
                }
              >
                {valueTypes.map((valueType) => (
                  <option key={valueType} value={valueType}>
                    {labelize(valueType)}
                  </option>
                ))}
              </select>
              <UnitInput
                aria-label="New assumption value"
                placeholder="Value"
                value={newAssumption.value}
                unit={newAssumption.unit}
                disabled={newAssumption.valueType === "null"}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    value: event.target.value,
                  })
                }
              />
              <Input
                aria-label="Assumption unit"
                placeholder="Unit"
                value={newAssumption.unit}
                onChange={(event) =>
                  setNewAssumption({
                    ...newAssumption,
                    unit: event.target.value,
                  })
                }
              />
            </div>
            <Button
              className="mt-2"
              variant="outline"
              disabled={
                !newAssumption.key.trim() ||
                !newAssumption.label.trim() ||
                !isValidValue(newAssumption.value, newAssumption.valueType) ||
                add.isPending
              }
              onClick={() => add.mutate()}
            >
              Add assumption
            </Button>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function AssumptionRow({
  tenant,
  caseId,
  scenarioId,
  assumption,
  readOnly,
  onSaved,
}: {
  tenant: TenantHeaders;
  caseId: string;
  scenarioId: string;
  assumption: ScenarioAssumptionRead;
  readOnly: boolean;
  onSaved: (message: string) => Promise<void>;
}) {
  const initialType = valueTypeOf(assumption.value);
  const [valueType, setValueType] = useState(initialType);
  const [value, setValue] = useState(
    valueInputForUnit(assumption.value, assumption.unit),
  );
  const [persistedValue, setPersistedValue] = useState(assumption.value);
  useEffect(() => {
    setValueType(valueTypeOf(assumption.value));
    setValue(valueInputForUnit(assumption.value, assumption.unit));
    setPersistedValue(assumption.value);
  }, [assumption.unit, assumption.updatedAt, assumption.value]);
  const nextValue = typedUnitValue(value, valueType, assumption.unit);
  const validValue = isValidValue(value, valueType);
  const dirty =
    valueType !== valueTypeOf(persistedValue) || nextValue !== persistedValue;
  const update = useMutation({
    mutationFn: () =>
      riskApi.updateAssumption(tenant, caseId, scenarioId, assumption.id, {
        value: nextValue,
        reason: "Reviewer updated assumption",
      }),
    onSuccess: () => {
      setPersistedValue(nextValue);
      return onSaved(`${assumption.label} saved`);
    },
  });
  const review = useMutation({
    mutationFn: () =>
      riskApi.reviewAssumption(tenant, caseId, scenarioId, assumption.id, {
        reason: "Reviewer approved assumption",
      }),
    onSuccess: () => onSaved(`${assumption.label} reviewed`),
  });
  return (
    <Fragment>
      <tr
        id={`scenario-${scenarioId}-assumption-${assumption.id}`}
        tabIndex={-1}
        className="border-b border-[rgb(var(--border))] outline-none last:border-0 focus:bg-amber-100"
      >
        <td className="min-w-0 px-2 py-1.5 align-middle">
          <div className="truncate text-sm font-medium">{assumption.label}</div>
          <div className="truncate text-[11px] text-[rgb(var(--muted-foreground))]">
            {labelize(assumption.category)} · {assumption.key}
          </div>
        </td>
        <td className="px-2 py-1.5 align-middle">
          <select
            aria-label={`${assumption.label} value type`}
            className="h-8 w-full rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2 text-xs"
            value={valueType}
            disabled={readOnly}
            onChange={(event) =>
              setValueType(event.target.value as AssumptionValueType)
            }
          >
            {valueTypes.map((option) => (
              <option key={option} value={option}>
                {labelize(option)}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-1.5 align-middle">
          <UnitInput
            aria-label={`${assumption.label} value`}
            value={value}
            unit={assumption.unit}
            disabled={readOnly || valueType === "null"}
            onChange={(event) => setValue(event.target.value)}
          />
        </td>
        <td className="truncate px-2 py-1.5 align-middle text-[rgb(var(--muted-foreground))]">
          {unitLabel(assumption.unit)}
        </td>
        <td className="px-2 py-1.5 align-middle">
          <Badge
            tone={
              assumption.reviewStatus === "reviewed" ? "success" : "warning"
            }
          >
            {labelize(assumption.reviewStatus)}
          </Badge>
        </td>
        <td className="px-2 py-1.5 align-middle">
          {!readOnly ? (
            <div className="flex gap-1.5">
              <Button
                size="sm"
                variant="outline"
                disabled={!dirty || !validValue || update.isPending}
                onClick={() => update.mutate()}
              >
                Save
              </Button>
              <Button
                size="sm"
                disabled={
                  dirty || !validValue || update.isPending || review.isPending
                }
                onClick={() => review.mutate()}
              >
                Review
              </Button>
            </div>
          ) : (
            <span className="text-[rgb(var(--muted-foreground))]">
              Read only
            </span>
          )}
        </td>
      </tr>
      {update.isError || review.isError ? (
        <tr>
          <td colSpan={6} className="p-2">
            <ErrorPanel error={update.error ?? review.error} />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

function UnitInput({
  unit,
  className,
  ...props
}: ComponentPropsWithoutRef<"input"> & { unit?: string | null }) {
  const suffix = unitSuffix(unit);
  return (
    <div className="relative min-w-0">
      <Input
        {...props}
        className={`${className ?? ""} ${suffix ? "pr-12" : ""}`}
      />
      {suffix ? (
        <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-xs font-medium text-[rgb(var(--muted-foreground))]">
          {suffix}
        </span>
      ) : null}
    </div>
  );
}

function unitSuffix(unit?: string | null) {
  if (!unit) return "";
  if (unit === "ratio" || unit === "percent" || unit === "%") return "%";
  if (unit === "days" || unit === "day") return "days";
  if (/^[A-Z]{3}$/.test(unit)) return unit;
  return "";
}

function unitLabel(unit?: string | null) {
  return unitSuffix(unit) || (unit ? labelize(unit) : "Unitless");
}

function scenarioDeepLink() {
  const targetId = workspaceHash();
  const prefix = "scenario-";
  const separator = "-assumption-";
  if (!targetId.startsWith(prefix) || !targetId.includes(separator))
    return null;
  const scenarioId = targetId.slice(prefix.length, targetId.indexOf(separator));
  const assumptionId = targetId.slice(
    targetId.indexOf(separator) + separator.length,
  );
  return isUuid(scenarioId) && isUuid(assumptionId)
    ? { scenarioId, targetId }
    : null;
}

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function valueTypeOf(value: AssumptionValue): AssumptionValueType {
  if (value === null) return "null";
  return typeof value as AssumptionValueType;
}

function valueInput(value: AssumptionValue): string {
  return value === null ? "" : String(value);
}

function valueInputForUnit(
  value: AssumptionValue,
  unit?: string | null,
): string {
  if (typeof value === "number" && unit === "ratio")
    return shiftDecimal(String(value), 2) ?? valueInput(value);
  return valueInput(value);
}

const decimalPattern =
  /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i;

function decimalParts(value: string) {
  return decimalPattern.exec(value.trim());
}

function shiftDecimal(value: string, places: number): string | null {
  const match = decimalParts(value);
  if (!match) return null;

  const integerDigits = match[2] ?? "0";
  const fractionDigits = match[2] === undefined ? match[4] : match[3];
  const digits = `${integerDigits}${fractionDigits ?? ""}`;
  const exponent = Number(match[5] ?? 0);
  const decimalIndex = integerDigits.length + exponent + places;
  if (!Number.isSafeInteger(decimalIndex) || Math.abs(decimalIndex) > 1_000)
    return null;
  const integer =
    decimalIndex <= 0
      ? "0"
      : `${digits.slice(0, decimalIndex)}${"0".repeat(
          Math.max(0, decimalIndex - digits.length),
        )}`;
  const fraction =
    decimalIndex <= 0
      ? `${"0".repeat(-decimalIndex)}${digits}`
      : digits.slice(decimalIndex);
  const normalizedInteger = integer.replace(/^0+(?=\d)/, "");
  const normalizedFraction = fraction.replace(/0+$/, "");
  return `${match[1] ?? ""}${normalizedInteger}${
    normalizedFraction ? `.${normalizedFraction}` : ""
  }`;
}

function isValidValue(value: string, valueType: AssumptionValueType): boolean {
  if (valueType === "number") {
    return decimalParts(value) !== null && Number.isFinite(Number(value));
  }
  if (valueType === "boolean") return value === "true" || value === "false";
  return true;
}

function typedValue(
  value: string,
  valueType: AssumptionValueType,
): AssumptionValue {
  if (valueType === "null") return null;
  if (valueType === "boolean") return value === "true";
  if (valueType === "number") return Number(value);
  return value;
}

function typedUnitValue(
  value: string,
  valueType: AssumptionValueType,
  unit?: string | null,
): AssumptionValue {
  if (valueType === "number" && unit === "ratio") {
    const shifted = shiftDecimal(value, -2);
    return shifted === null ? Number.NaN : Number(shifted);
  }
  return typedValue(value, valueType);
}
