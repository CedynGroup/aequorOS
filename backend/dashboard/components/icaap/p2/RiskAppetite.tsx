"use client";

/**
 * The risk appetite statement: appetite, tolerance, capacity, the current
 * reading, the RAG and the direction, for every metric the bank has defined.
 *
 * Three rules this screen is built around.
 *
 * THE SERVER STAYS AUTHORITATIVE. The ordering rule is enforced by the API,
 * which answers 422 `appetite_ordering_invalid {violations, reference}`. Those
 * violation sentences are shown exactly as they arrive. The client mirror in
 * `lib/icaap/appetite.ts` only moves the same message earlier, while the
 * preparer is typing; it never decides that a save will succeed.
 *
 * D-036: a metric with no governed regulatory value reads "not assessed against
 * a regulatory floor". It is not an error, it is not a refusal, and a floor is
 * never invented to fill the gap.
 *
 * `rag: "none"` IS NOT GREEN. The contract's "not evaluated" renders as an
 * explicit neutral state — a green badge on an unevaluated metric would read as
 * a compliance affirmation of something nobody measured.
 */

import { useState } from "react";
import { Plus } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import { isApiError } from "@/lib/api/client";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  useCreateIcaapAppetiteMetric,
  useIcaapAppetite,
  useUpdateIcaapAppetiteMetric,
  type IcaapAppetiteMetricDef,
  type IcaapAppetiteMetric,
  type IcaapAppetite,
} from "@/lib/api/icaapRiskCapital";
import AppetiteScale from "./AppetiteScale";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import ParameterProvenance from "./ParameterProvenance";
import { ICON_SM, REASON_MAX, ROWS_MEDIUM } from "./display";
import {
  NOT_ASSESSED,
  NO_REGULATORY_FLOOR,
  ORDERING_RULE,
  directionLabel,
  fmtInUnit,
  fmtPercent,
  ragCopy,
  trendLabel,
} from "./labels";

export default function RiskAppetite({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canEdit = scope.capitalEdit === true;
  const appetiteQuery = useIcaapAppetite(bankId, cycleId);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<IcaapAppetiteMetric | null>(null);

  const appetite = appetiteQuery.data;
  const metrics = appetite?.metrics ?? [];
  const parameters = appetite?.parameters ?? [];

  const unavailable = p2UnavailableNotice(appetiteQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Risk appetite" message={unavailable} />;
  }

  return (
    <QueryBoundary
      isLoading={appetiteQuery.isLoading}
      error={appetiteQuery.error}
      onRetry={() => void appetiteQuery.refetch()}
      contained
    >
      {appetite && (
        <div className="space-y-4">
          <SectionCard
            title="Risk appetite"
            subtitle={ORDERING_RULE}
            actions={
              canEdit ? (
                <SecondaryButton onClick={() => setAdding(true)}>
                  <Plus size={ICON_SM} aria-hidden />
                  Add a metric
                </SecondaryButton>
              ) : undefined
            }
          >
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Summary
                label="Metrics defined"
                value={String(appetite.summary?.metricCount ?? 0)}
              />
              <Summary
                label="Beyond appetite"
                value={String(appetite.summary?.amberCount ?? 0)}
              />
              <Summary
                label="Beyond tolerance"
                value={String(appetite.summary?.breachCount ?? 0)}
              />
              <Summary
                label="Qualitative"
                value={String(appetite.summary?.qualitativeCount ?? 0)}
              />
            </dl>
          </SectionCard>

          {metrics.length === 0 ? (
            <EmptyState
              title="No appetite metrics yet"
              description="A risk appetite statement names the measures the board governs the bank by, and the level at which each one becomes a concern."
            />
          ) : (
            metrics.map((metric) => (
              <MetricCard
                key={metric.metricId}
                metric={metric}
                canEdit={canEdit}
                onEdit={() => setEditing(metric)}
              />
            ))
          )}

          {parameters.length > 0 && (
            <SectionCard
              title="Where the regulatory values come from"
              subtitle="Each one is a control-plane row, shown with its citation and confirmation status."
            >
              <ParameterProvenance uses={parameters} />
            </SectionCard>
          )}
        </div>
      )}

      {adding && appetite && (
        <MetricDialog
          bankId={bankId}
          cycleId={cycleId}
          appetite={appetite}
          metric={null}
          onClose={() => setAdding(false)}
        />
      )}
      {editing && appetite && (
        <MetricDialog
          bankId={bankId}
          cycleId={cycleId}
          appetite={appetite}
          metric={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </QueryBoundary>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-caption text-slate">{label}</dt>
      <dd className="text-h3 tnum text-navy">{value}</dd>
    </div>
  );
}

function MetricCard({
  metric,
  canEdit,
  onEdit,
}: {
  metric: IcaapAppetiteMetric;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const rag = ragCopy(metric.evaluation?.rag);
  const trend = trendLabel(metric.evaluation?.trend);

  return (
    <SectionCard
      title={metric.title}
      subtitle={directionLabel(metric.direction)}
      actions={
        <div className="flex items-center gap-2">
          <StatusPill tone={rag.tone}>{rag.label}</StatusPill>
          {canEdit && (
            <SecondaryButton onClick={onEdit}>Edit levels</SecondaryButton>
          )}
        </div>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-body">
            <div>
              <dt className="text-caption text-slate">Current</dt>
              <dd className="tnum text-navy">
                {fmtInUnit(
                  metric.evaluation?.currentValue,
                  metric.unit,
                  NOT_ASSESSED,
                )}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-slate">Utilisation</dt>
              <dd className="tnum text-navy">
                {fmtPercent(metric.evaluation?.utilisationPct)}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-slate">
                Headroom to appetite
              </dt>
              <dd className="tnum text-navy">
                {fmtInUnit(
                  metric.evaluation?.headroomToAppetite,
                  metric.unit,
                  NOT_ASSESSED,
                )}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-slate">Direction of travel</dt>
              <dd>
                <StatusPill tone={trend.tone}>{trend.label}</StatusPill>
              </dd>
            </div>
          </dl>

          {metric.boardRegisterValue !== null && (
            <p className="mt-3 text-caption text-slate">
              The board&apos;s own register holds{" "}
              {fmtInUnit(metric.boardRegisterValue, metric.unit)} for this
              metric.
            </p>
          )}

          {metric.qualitativeStatement && (
            <p className="mt-3 text-body text-navy/80">
              {metric.qualitativeStatement}
            </p>
          )}

          {(metric.violations ?? []).length > 0 && (
            <ul className="mt-3 space-y-1 text-caption text-critical">
              {(metric.violations ?? []).map((violation) => (
                <li key={violation}>{violation}</li>
              ))}
            </ul>
          )}
        </div>

        <AppetiteScale
          direction={metric.direction}
          unit={metric.unit}
          appetite={metric.appetite}
          tolerance={metric.tolerance}
          capacity={metric.capacity}
          reference={metric.regulatoryReference?.value ?? null}
          current={metric.evaluation?.currentValue ?? null}
          referenceMissing={metric.referenceMissing}
        />
      </div>

      {metric.regulatoryReference && (
        <div className="mt-3">
          <ParameterProvenance
            uses={[
              {
                paramCode: metric.regulatoryReference.paramCode,
                value: metric.regulatoryReference.value,
                valueJson: null,
                unit: metric.unit,
                confirmationStatus: metric.regulatoryReference.confirmationStatus,
                representative: metric.regulatoryReference.representative,
                resolved: metric.regulatoryReference.value !== null,
                sourceCitation: metric.regulatoryReference.sourceCitation,
                effectiveFrom: metric.regulatoryReference.effectiveFrom,
                parameterId: null,
                // The metric's own reference carries no role list; the register
                // on the Pillar 2 tab is where a value's uses are stated.
                roles: [],
              },
            ]}
            compact
          />
        </div>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Define / edit one metric
// ---------------------------------------------------------------------------

function MetricDialog({
  bankId,
  cycleId,
  appetite,
  metric,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  appetite: IcaapAppetite;
  metric: IcaapAppetiteMetric | null;
  onClose: () => void;
}) {
  const create = useCreateIcaapAppetiteMetric(bankId, cycleId);
  const update = useUpdateIcaapAppetiteMetric(bankId, cycleId);
  const mutation = metric ? update : create;

  const catalogue = appetite.catalogue ?? [];
  const defined = appetite.metrics ?? [];
  const available = catalogue.filter(
    (definition) =>
      metric?.metricKey === definition.metricKey ||
      !defined.some((m) => m.metricKey === definition.metricKey),
  );

  const [metricKey, setMetricKey] = useState(
    metric?.metricKey ?? available[0]?.metricKey ?? "",
  );
  const [appetiteValue, setAppetiteValue] = useState(metric?.appetite ?? "");
  const [tolerance, setTolerance] = useState(metric?.tolerance ?? "");
  const [capacity, setCapacity] = useState(metric?.capacity ?? "");
  const [statement, setStatement] = useState(
    metric?.qualitativeStatement ?? "",
  );
  const [reason, setReason] = useState("");

  const definition: IcaapAppetiteMetricDef | undefined =
    available.find((d) => d.metricKey === metricKey) ??
    catalogue.find((d) => d.metricKey === metricKey);

  const reference = metric?.regulatoryReference ?? null;
  const referenceMissing = metric?.referenceMissing ?? reference === null;

  /**
   * The server's own violation sentences, when it refused the save. They are
   * shown verbatim — the client mirror's wording never replaces them.
   */
  const serverViolations =
    isApiError(mutation.error) &&
    Array.isArray(
      (mutation.error.details as { violations?: unknown } | undefined)
        ?.violations,
    )
      ? ((mutation.error.details as { violations: string[] }).violations)
      : [];

  const submit = () => {
    const levels = {
      appetiteValue: appetiteValue.trim() === "" ? null : appetiteValue.trim(),
      toleranceValue: tolerance.trim() === "" ? null : tolerance.trim(),
      capacityValue: capacity.trim() === "" ? null : capacity.trim(),
      reason: reason.trim(),
    };
    if (metric) {
      update.mutate(
        {
          metricId: metric.metricId,
          // The row revision the form READ. The server refuses a stale one
          // rather than overwriting someone else's edit.
          payload: {
            ...levels,
            baseRev: metric.rowRev,
            qualitativeStatement:
              statement.trim() === "" ? undefined : statement.trim(),
          },
        },
        { onSuccess: onClose },
      );
    } else {
      create.mutate(
        {
          ...levels,
          metricKey,
          label: definition?.title ?? metricKey,
          measureKind: "quantitative",
          qualitativeStatement: statement.trim(),
        },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Dialog
      title={metric ? metric.title : "Add an appetite metric"}
      description={ORDERING_RULE}
      onClose={onClose}
      wide
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            onClick={submit}
            disabled={
              mutation.isPending || metricKey === "" || reason.trim() === ""
            }
          >
            {mutation.isPending ? "Saving…" : "Save"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <div className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            <p>{(mutation.error as Error).message}</p>
            {serverViolations.length > 0 && (
              <ul className="mt-2 space-y-1">
                {serverViolations.map((violation) => (
                  <li key={violation}>{violation}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {!metric && (
          <FieldLabel label="Metric">
            <select
              aria-label="Metric"
              value={metricKey}
              onChange={(event) => setMetricKey(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            >
              {available.map((entry) => (
                <option key={entry.metricKey} value={entry.metricKey}>
                  {entry.title}
                </option>
              ))}
            </select>
          </FieldLabel>
        )}

        {definition && (
          <p className="text-caption text-slate">
            {directionLabel(definition.direction)}.
          </p>
        )}

        <p className="text-caption text-slate">
          {referenceMissing
            ? `No regulatory value is governed for this metric, so the capacity is ${NO_REGULATORY_FLOOR}.`
            : `The governing regulatory value is ${fmtInUnit(reference?.value, metric?.unit ?? definition?.unit)}.`}
        </p>

        <div className="grid gap-3 sm:grid-cols-3">
          <FieldLabel label="Appetite">
            <input
              inputMode="decimal"
              value={appetiteValue}
              onChange={(event) => setAppetiteValue(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body tnum"
            />
          </FieldLabel>
          <FieldLabel label="Tolerance">
            <input
              inputMode="decimal"
              value={tolerance}
              onChange={(event) => setTolerance(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body tnum"
            />
          </FieldLabel>
          <FieldLabel label="Capacity">
            <input
              inputMode="decimal"
              value={capacity}
              onChange={(event) => setCapacity(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body tnum"
            />
          </FieldLabel>
        </div>

        {definition && (
          <AppetiteScale
            direction={definition.direction}
            unit={definition.unit}
            appetite={appetiteValue}
            tolerance={tolerance}
            capacity={capacity}
            reference={reference?.value ?? null}
            current={metric?.evaluation?.currentValue ?? null}
            referenceMissing={referenceMissing}
          />
        )}

        <FieldLabel label="Qualitative statement (optional)">
          <textarea
            value={statement}
            rows={ROWS_MEDIUM}
            onChange={(event) => setStatement(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel
          label="Reason for this change"
          hint="Recorded in the audit trail."
        >
          <input
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
