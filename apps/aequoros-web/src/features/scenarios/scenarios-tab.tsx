import type {
  AssumptionCategory,
  AssumptionValue,
  ScenarioAssumptionRead,
  ScenarioRead,
} from "@aequoros/risk-service-api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
}: {
  tenant: TenantHeaders;
  caseId: string;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["scenarios", tenant, caseId] as const;
  const query = useQuery({
    queryKey,
    queryFn: () => riskApi.scenarios(tenant, caseId),
  });
  const [selectedId, setSelectedId] = useState("");
  const [customName, setCustomName] = useState("");
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    if (!selectedId && query.data?.scenarios[0])
      setSelectedId(query.data.scenarios[0].id);
  }, [query.data, selectedId]);

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
            disabled={initialize.isPending}
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
  return (
    <div className="space-y-3">
      {savedMessage ? (
        <Alert title="Saved successfully">{savedMessage}</Alert>
      ) : null}
      <div className="grid gap-3 lg:grid-cols-[240px_minmax(0,1fr)]">
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
                </span>
              </button>
            ))}
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
          </div>
        </Panel>
        <ScenarioEditor
          key={selected.id}
          tenant={tenant}
          caseId={caseId}
          scenario={selected}
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
  onSaved,
  onSelected,
}: {
  tenant: TenantHeaders;
  caseId: string;
  scenario: ScenarioRead;
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
        value: typedValue(newAssumption.value, newAssumption.valueType),
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

  return (
    <Panel>
      <PanelHeader
        title={scenario.name}
        meta={`${labelize(scenario.scenarioType)} scenario`}
        actions={
          <Badge tone="info">{scenario.assumptions.length} inputs</Badge>
        }
      />
      <div className="space-y-4 p-3">
        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] md:items-end">
          <div>
            <Label>Scenario name</Label>
            <Input
              aria-label="Scenario name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <Label>Scenario description</Label>
            <Textarea
              aria-label="Scenario description"
              className="min-h-8"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
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
        </div>
        {validation.isLoading ? <Skeleton className="h-16" /> : null}
        {validation.isError ? <ErrorPanel error={validation.error} /> : null}
        {validation.data?.complete ? (
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
        {actionError ? <ErrorPanel error={actionError} /> : null}
        <div className="space-y-2">
          {scenario.assumptions.length ? (
            scenario.assumptions.map((assumption) => (
              <AssumptionRow
                key={assumption.id}
                tenant={tenant}
                caseId={caseId}
                scenarioId={scenario.id}
                assumption={assumption}
                onSaved={onSaved}
              />
            ))
          ) : (
            <Alert title="No assumptions">
              Add required growth, expense, cash-flow timing, credit usage, and
              repayment inputs.
            </Alert>
          )}
        </div>
        <div className="rounded-md border border-dashed border-[rgb(var(--border))] p-3">
          <div className="mb-2 text-sm font-medium">Add assumption</div>
          <div className="grid gap-2 md:grid-cols-6">
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
                setNewAssumption({ ...newAssumption, key: event.target.value })
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
            <Input
              aria-label="New assumption value"
              placeholder="Value"
              value={newAssumption.value}
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
                setNewAssumption({ ...newAssumption, unit: event.target.value })
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
      </div>
    </Panel>
  );
}

function AssumptionRow({
  tenant,
  caseId,
  scenarioId,
  assumption,
  onSaved,
}: {
  tenant: TenantHeaders;
  caseId: string;
  scenarioId: string;
  assumption: ScenarioAssumptionRead;
  onSaved: (message: string) => Promise<void>;
}) {
  const initialType = valueTypeOf(assumption.value);
  const [valueType, setValueType] = useState(initialType);
  const [value, setValue] = useState(valueInput(assumption.value));
  const [persistedValue, setPersistedValue] = useState(assumption.value);
  useEffect(() => {
    setValueType(valueTypeOf(assumption.value));
    setValue(valueInput(assumption.value));
    setPersistedValue(assumption.value);
  }, [assumption.updatedAt, assumption.value]);
  const nextValue = typedValue(value, valueType);
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
    <div className="grid gap-2 rounded-md border border-[rgb(var(--border))] p-2 md:grid-cols-[minmax(0,1fr)_100px_160px_auto] md:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium">
            {assumption.label}
          </span>
          <Badge
            tone={
              assumption.reviewStatus === "reviewed" ? "success" : "warning"
            }
          >
            {assumption.reviewStatus}
          </Badge>
        </div>
        <div className="text-xs text-[rgb(var(--muted-foreground))]">
          {labelize(assumption.category)} · {assumption.key} ·{" "}
          {assumption.unit ?? "unitless"}
        </div>
      </div>
      <select
        aria-label={`${assumption.label} value type`}
        className="h-8 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] px-2 text-sm"
        value={valueType}
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
      <Input
        aria-label={`${assumption.label} value`}
        value={value}
        disabled={valueType === "null"}
        onChange={(event) => setValue(event.target.value)}
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={
            !dirty || !isValidValue(value, valueType) || update.isPending
          }
          onClick={() => update.mutate()}
        >
          Save
        </Button>
        <Button
          size="sm"
          disabled={dirty || update.isPending || review.isPending}
          onClick={() => review.mutate()}
        >
          Review
        </Button>
      </div>
      {update.isError || review.isError ? (
        <div className="md:col-span-4">
          <ErrorPanel error={update.error ?? review.error} />
        </div>
      ) : null}
    </div>
  );
}

function valueTypeOf(value: AssumptionValue): AssumptionValueType {
  if (value === null) return "null";
  return typeof value as AssumptionValueType;
}

function valueInput(value: AssumptionValue): string {
  return value === null ? "" : String(value);
}

function isValidValue(value: string, valueType: AssumptionValueType): boolean {
  if (valueType === "number") {
    return value.trim() !== "" && Number.isFinite(Number(value));
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
